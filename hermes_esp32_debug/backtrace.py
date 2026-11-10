"""Parse ESP32 panic output and resolve addresses to ``function @ file:line``.

The patterns follow what ESP-IDF and the Arduino-ESP32 core print on a panic
(see ``NOTICE`` for the upstream projects whose behaviour this mirrors).
``addr2line`` from the Espressif toolchain is run as a subprocess; nothing is
imported from GPL code.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .proc import run_isolated

# --- panic text patterns ---------------------------------------------------

GURU_RE = re.compile(r"Guru Meditation Error:\s*Core\s+(?P<core>\d+)\s+panic'ed\s+\((?P<reason>[^)]+)\)(?P<rest>[^\r\n]*)")
BACKTRACE_LINE_RE = re.compile(r"Backtrace:\s*(?P<body>[^\r\n]*)")
PC_SP_RE = re.compile(r"0x(?P<pc>[0-9a-fA-F]{8}):0x(?P<sp>[0-9a-fA-F]{8})")
CORRUPTED_RE = re.compile(r"CORRUPTED|\|<-CORRUPTED")
REG_RE = re.compile(r"\b(?P<name>PC|EXCVADDR|EXCCAUSE|PS|A0|MEPC|RA|MCAUSE|MTVAL|SP)\s*:\s*0x(?P<val>[0-9a-fA-F]{8})")
ABORT_RE = re.compile(r"abort\(\) was called at PC 0x(?P<pc>[0-9a-fA-F]{8}) on core (?P<core>\d+)")
ASSERT_RE = re.compile(r"(?:assert failed|ESP_ERROR_CHECK failed):\s*(?P<what>[^\r\n]+)")
ELF_SHA_RE = re.compile(r"ELF file SHA256:\s*(?P<sha>[0-9a-fA-F]{8,64})")
ROM_CHIP_RE = re.compile(r"ESP-ROM:(?P<chip>esp32[a-z0-9]*)", re.IGNORECASE)
RST_RE = re.compile(r"rst:0x(?P<code>[0-9a-fA-F]+)\s*\((?P<name>[A-Z_0-9]+)\)")
WDT_RE = re.compile(r"Task watchdog got triggered|Interrupt wdt timeout|TG\dWDT_SYS_RST|RTCWDT_RTC_RST", re.IGNORECASE)
STACK_RE = re.compile(r"Stack canary watchpoint triggered \((?P<task>[^)]+)\)|stack overflow in task (?P<task2>\S+)", re.IGNORECASE)
BROWNOUT_RE = re.compile(r"Brownout detector was triggered", re.IGNORECASE)
COREDUMP_NOISE_RE = re.compile(r"esp_core_dump\w*: (?:No core dump partition found|Core dump flash config is corrupted|Elf write init failed|Core dump write failed)")

XTENSA_EXCCAUSE = {
    0: "IllegalInstruction", 2: "InstructionFetchError", 3: "LoadStoreError",
    4: "Level1Interrupt", 6: "IntegerDivideByZero", 9: "LoadStoreAlignment",
    12: "InstrPIFDataError", 13: "LoadStorePIFDataError", 14: "InstrPIFAddrError",
    15: "LoadStorePIFAddrError", 20: "InstFetchProhibited", 28: "LoadProhibited",
    29: "StoreProhibited",
}
RISCV_MCAUSE = {
    0: "Instruction address misaligned", 1: "Instruction access fault", 2: "Illegal instruction",
    3: "Breakpoint", 4: "Load address misaligned", 5: "Load access fault",
    6: "Store address misaligned", 7: "Store access fault", 11: "Environment call from M-mode",
}


def _is_code_addr(addr: int) -> bool:
    """Rough filter for addresses in IRAM / flash-mapped instruction space."""
    return 0x40000000 <= addr < 0x50000000


@dataclass
class PanicInfo:
    chip_hint: Optional[str] = None
    arch_hint: Optional[str] = None
    reason: Optional[str] = None
    core: Optional[int] = None
    registers: dict[str, str] = field(default_factory=dict)
    backtrace: list[str] = field(default_factory=list)
    backtrace_corrupted: bool = False
    abort_pc: Optional[str] = None
    assert_text: Optional[str] = None
    elf_sha256_prefix: Optional[str] = None
    reset_reasons: list[str] = field(default_factory=list)
    stack_overflow_task: Optional[str] = None
    watchdog: bool = False
    brownout: bool = False
    coredump_unavailable: bool = False
    panic_count: int = 0

    def addresses(self) -> list[str]:
        """Addresses to resolve, in order, de-duplicated."""
        seen: list[str] = []
        pc = int(self.registers["PC"], 16) if "PC" in self.registers else None
        bt = list(self.backtrace)
        # ESP-IDF prints the first Xtensa backtrace entry as PC adjusted by up to 3 bytes
        # (esp_cpu_process_stack_pc). It is the same frame as the PC register, and resolving it
        # separately yields a misleading second line (often the function's opening brace).
        if pc is not None and bt and 0 <= pc - int(bt[0], 16) <= 3:
            bt = bt[1:]
        for a in ([self.registers.get("PC")] if "PC" in self.registers else []) + \
                 ([self.registers.get("MEPC")] if "MEPC" in self.registers else []) + \
                 ([self.registers.get("RA")] if "RA" in self.registers else []) + \
                 ([self.abort_pc] if self.abort_pc else []) + bt:
            if a and a not in seen and _is_code_addr(int(a, 16)):
                seen.append(a)
        return seen

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, [], {}, False, 0)}
        d["panic_count"] = self.panic_count
        return d


def parse_panic(text: str, *, which: str = "last") -> PanicInfo:
    """Parse the last (default) or first panic block in ``text``."""
    text = text or ""
    info = PanicInfo()
    rom = ROM_CHIP_RE.search(text)
    if rom:
        info.chip_hint = rom.group("chip").lower()
    info.reset_reasons = [m.group("name") for m in RST_RE.finditer(text)]
    info.brownout = bool(BROWNOUT_RE.search(text))
    info.watchdog = bool(WDT_RE.search(text))
    info.coredump_unavailable = bool(COREDUMP_NOISE_RE.search(text))

    gurus = list(GURU_RE.finditer(text))
    bts = list(BACKTRACE_LINE_RE.finditer(text))
    aborts = list(ABORT_RE.finditer(text))
    info.panic_count = max(len(gurus), len(bts), len(aborts))
    if not (gurus or bts or aborts or "MEPC" in text):
        return info

    # Pick the block: from the chosen panic header to the next header / reboot marker.
    headers = sorted(m.start() for m in gurus + aborts)
    if headers:
        start = headers[-1] if which == "last" else headers[0]
    elif bts:
        start = (bts[-1] if which == "last" else bts[0]).start()
    else:
        start = text.find("MEPC")
    later = [h for h in headers if h > start]
    end = later[0] if later else len(text)
    reboot = re.search(r"Rebooting\.\.\.|\bets [A-Z][a-z]{2} |ESP-ROM:|rst:0x", text[start:end])
    if reboot:
        end = start + reboot.start()
    block = text[start:end]

    g = GURU_RE.search(block)
    if g:
        info.reason = g.group("reason").strip()
        info.core = int(g.group("core"))
    a = ABORT_RE.search(block)
    if a:
        info.abort_pc = "0x" + a.group("pc").lower()
        info.core = int(a.group("core")) if info.core is None else info.core
        info.reason = info.reason or "abort() called"
    asrt = ASSERT_RE.search(text[max(0, start - 400):end])
    if asrt:
        info.assert_text = asrt.group("what").strip()
    for m in REG_RE.finditer(block):
        info.registers.setdefault(m.group("name"), "0x" + m.group("val").lower())
    if "MEPC" in info.registers or "MCAUSE" in info.registers:
        info.arch_hint = "riscv"
    elif "EXCCAUSE" in info.registers or PC_SP_RE.search(block):
        info.arch_hint = "xtensa"
    b = BACKTRACE_LINE_RE.search(block)
    if b:
        body = b.group("body")
        info.backtrace = ["0x" + m.group("pc").lower() for m in PC_SP_RE.finditer(body)]
        if not info.backtrace:  # RISC-V frame-pointer backtraces: "Backtrace: 0x42000abc 0x..."
            info.backtrace = ["0x" + x.lower() for x in re.findall(r"0x([0-9a-fA-F]{8})", body)]
        info.backtrace_corrupted = bool(CORRUPTED_RE.search(body))
    sha = ELF_SHA_RE.search(text[start:]) or ELF_SHA_RE.search(text)
    if sha:
        info.elf_sha256_prefix = sha.group("sha").lower()
    so = STACK_RE.search(text)
    if so:
        info.stack_overflow_task = (so.group("task") or so.group("task2") or "").strip()
    return info


# --- exception decoding & hints -------------------------------------------

def describe_exception(info: PanicInfo) -> Optional[str]:
    regs = info.registers
    if "EXCCAUSE" in regs:
        code = int(regs["EXCCAUSE"], 16)
        return f"EXCCAUSE {code} = {XTENSA_EXCCAUSE.get(code, 'unknown')}"
    if "MCAUSE" in regs:
        code = int(regs["MCAUSE"], 16) & 0x7FFFFFFF
        return f"MCAUSE {code} = {RISCV_MCAUSE.get(code, 'unknown')}"
    return None


def hints(info: PanicInfo) -> list[str]:
    """Heuristic first-look hints. They are hypotheses for the agent, not verdicts."""
    out: list[str] = []
    fault_addr = info.registers.get("EXCVADDR") or info.registers.get("MTVAL")
    reason = (info.reason or "").lower()
    if fault_addr and int(fault_addr, 16) < 0x1000 and (
        "loadprohibited" in reason or "storeprohibited" in reason or "access fault" in reason
        or "EXCVADDR" in info.registers or "MTVAL" in info.registers
    ):
        out.append(
            f"Fault address {fault_addr} is near 0: most likely a NULL (or NULL+offset) pointer "
            "dereference in the first resolved frame. Check pointers/objects used there before use."
        )
    if info.stack_overflow_task:
        out.append(
            f"Stack overflow in task '{info.stack_overflow_task}': raise that task's stack size "
            "(xTaskCreate stack depth / CONFIG_ARDUINO_LOOP_STACK_SIZE) or move big buffers off the stack."
        )
    if info.watchdog:
        out.append(
            "Watchdog reset: a task or ISR blocked too long. Look for busy loops without "
            "vTaskDelay()/delay(), long blocking I/O, or a deadlocked mutex in the resolved frames."
        )
    if info.brownout:
        out.append("Brownout reset: the supply voltage dipped (USB cable/port, servo or speaker current). Not a code bug.")
    if info.assert_text:
        out.append(f"Assertion failed: {info.assert_text}")
    if info.abort_pc and not info.assert_text:
        out.append("abort() was called: look a few lines above the panic for the error message (ESP_ERROR_CHECK, assert, std::bad_alloc).")
    if "illegalinstruction" in reason or "instfetchprohibited" in reason:
        out.append("IllegalInstruction/InstFetchProhibited: usually a corrupted function pointer, stack corruption, or calling into freed memory.")
    if info.backtrace_corrupted:
        out.append("Backtrace is marked CORRUPTED: the stack itself was damaged; the first frames are still the most trustworthy.")
    if info.coredump_unavailable and (info.backtrace or info.reason or info.abort_pc):
        out.append("The 'esp_core_dump ... failed / no core dump partition' errors are a side effect of the partition "
                   "table having no coredump partition. They are not the cause of the crash; the backtrace is.")
    if len(info.reset_reasons) >= 3:
        out.append(f"{len(info.reset_reasons)} resets seen in this capture: the board is boot-looping.")
    return out


# --- ELF & addr2line --------------------------------------------------------

def check_elf(path: str) -> tuple[bool, str, Optional[str]]:
    """Return ``(ok, message, sha256_hex)``. Reads the file; never writes."""
    p = Path(path).expanduser()
    if not p.is_file():
        return False, f"ELF file not found: {p}", None
    try:
        with p.open("rb") as fh:
            magic = fh.read(4)
            if magic != b"\x7fELF":
                return False, f"Not an ELF file (bad magic): {p}. Use the .elf from the build, not the .bin.", None
            fh.seek(0)
            h = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return True, "ok", h.hexdigest()
    except OSError as exc:
        return False, f"Cannot read ELF {p}: {exc}", None


FRAME_RE = re.compile(r"^(?P<addr>0x[0-9a-fA-F]+):\s*(?P<func>.*?) at (?P<loc>.+)$")
INLINE_RE = re.compile(r"^\s*\(inlined by\)\s*(?P<func>.*?) at (?P<loc>.+)$")


def _clean_path(path: str) -> str:
    """Windows builds mix separators ("C:\\sketch/crash_demo.cpp"); use backslashes for drive paths."""
    if re.match(r"^[A-Za-z]:[\\/]", path):
        return path.replace("/", "\\")
    return path


def _split_loc(loc: str) -> tuple[str, Optional[int]]:
    loc = loc.strip()
    loc = re.sub(r"\s*\(discriminator \d+\)$", "", loc)
    m = re.match(r"^(?P<file>.*):(?P<line>\d+|\?)$", loc)
    if not m:
        return loc, None
    line = m.group("line")
    return _clean_path(m.group("file")), (int(line) if line.isdigit() else None)


def parse_addr2line_output(output: str) -> list[dict]:
    frames: list[dict] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = FRAME_RE.match(line)
        if m:
            file, lineno = _split_loc(m.group("loc"))
            func = m.group("func").strip()
            frames.append({
                "address": m.group("addr").lower(),
                "function": None if func == "??" else func,
                "file": None if file in ("??", "") else file,
                "line": lineno if lineno else None,
                "inlined_by": [],
            })
            continue
        m = INLINE_RE.match(line)
        if m and frames:
            file, lineno = _split_loc(m.group("loc"))
            frames[-1]["inlined_by"].append({"function": m.group("func").strip(), "file": file, "line": lineno})
            continue
        if line.startswith("0x") and frames is not None:  # "0x4200: ?? ??:0" variants
            addr = line.split(":", 1)[0]
            frames.append({"address": addr.lower(), "function": None, "file": None, "line": None, "inlined_by": []})
    return frames


def run_addr2line(addr2line: str, elf: str, addresses: list[str], timeout: float = 20.0,
                  cwd=None) -> tuple[list[dict], Optional[str]]:
    if not addresses:
        return [], None
    cmd = [addr2line, "-pfiaC", "-e", elf, *addresses]
    try:
        proc = run_isolated(cmd, timeout=timeout, cwd=cwd, runner=subprocess.run)
    except FileNotFoundError:
        return [], f"addr2line not found: {addr2line}"
    except subprocess.TimeoutExpired:
        return [], f"addr2line timed out after {timeout:.0f}s"
    except OSError as exc:
        return [], f"addr2line failed to start: {exc}"
    if proc.returncode != 0:
        return [], f"addr2line exited {proc.returncode}: {(proc.stderr or '').strip()[:400]}"
    return parse_addr2line_output(proc.stdout), None


def source_snippet(file: str, line: int, context: int = 3, max_line_len: int = 200) -> Optional[dict]:
    """Read a few lines around ``file:line`` if the source exists on this machine."""
    try:
        p = Path(file)
        if not p.is_file() or line < 1:
            return None
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        lo, hi = max(1, line - context), min(len(lines), line + context)
        return {
            "file": str(p),
            "start_line": lo,
            "lines": [f"{'>' if n == line else ' '}{n:5d} | {lines[n - 1][:max_line_len]}" for n in range(lo, hi + 1)],
        }
    except (OSError, ValueError):
        return None
