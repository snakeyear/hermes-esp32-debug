# 90-second demo script — "My StackChan just crashed"

Real, verified flow (2026-11-09, Windows 11 P5070P, Hermes v0.23.0, esp32-debug 0.1.2).

**Hardware:** M5Stack StackChan (CoreS3, ESP32-S3 rev v0.2, 16MB flash) on **COM7** (native USB-Serial/JTAG `303a:1001`).
**Firmware:** `C:\GdriveM\ESP32\StackChanAssistant-crashdemo` — the normal StackChanAssistant app plus a deliberate
NULL-pointer read. **Tap the screen** (not the head touch strip) → `LoadProhibited`.
**ELF:** `C:\GdriveM\ESP32\StackChanAssistant-crashdemo\build\m5stack.esp32.m5stack_cores3\StackChanAssistant-crashdemo.ino.elf`
(SHA256 `2c9c09f1…`, matches the board's `ELF file SHA256: 2c9c09f13`).

## Before recording
- Close Arduino Serial Monitor / any terminal on COM7 (the plugin reports `port_busy` otherwise).
- Board booted and showing the face (≈ 6 s after reset).
- Hide the taskbar clock / user name. The SSID is never printed on serial by this firmware; the MAC appears in
  `esp32_chip_info` — blur it or skip that card.
- Hermes tool calls take a few seconds each (model round-trip); cut the waiting in editing, or speed it up 2×.

## Shot list (≈ 85 s)

| Time | Screen / action | Voice-over / caption |
| --- | --- | --- |
| 0:00–0:06 | StackChan on camera, face blinking. | "This is my StackChan. Let's make it crash — and let Hermes find out why." |
| 0:06–0:14 | Type: **"My StackChan on USB crashes when I tap the screen. Listen to it for 30 seconds while I tap it."** | — |
| 0:14–0:20 | `esp32_list_ports` card → `COM7`, `303a:1001`, *Espressif native USB*, confidence high. | "It finds the board by its USB ID." |
| 0:20–0:34 | `esp32_serial_capture(port=COM7, seconds=30)` is running → **tap the screen** on camera → face freezes, board reboots. Result: `panic_detected: true`, `stop_reason: panic_captured`, `exception: EXCCAUSE 28 = LoadProhibited`, `fault_address: 0x00000006`, `elf_sha256_prefix: 2c9c09f13`. | "It catches the Guru Meditation, read-only — and redacts Wi-Fi names, tokens and IPs." |
| 0:34–0:40 | Type: **"Decode it. The ELF is C:\GdriveM\ESP32\StackChanAssistant-crashdemo\build\…\StackChanAssistant-crashdemo.ino.elf"** | — |
| 0:40–0:58 | `esp32_decode_backtrace(elf_path=…)` (no log pasted: `log_source: last_capture (COM7, …)`) → `elf_sha256_match: true`; frames: **averageAccel() crash_demo.cpp:25** → processImuFrame() :32 → triggerCrashDemo() :40 → crashDemoPoll() :85 → loop() .ino:1056 → loopTask (framework). Source snippet with `>   25 \|  int32_t sum = frame->accel[0] + …`. | "Address to file and line — and it checks the ELF really is the firmware on the board." |
| 0:58–0:72 | Hermes' answer: "`latestImuFrame()` returns NULL; `averageAccel()` reads `frame->accel[]` → EXCVADDR 0x6 = NULL + offset. Fix: check `if (!frame) return 0;` or initialise the IMU frame before use." Show the 3-line diff. Ignore the core-dump errors (hint says they're a missing partition, not the cause). | "Root cause and a concrete fix." |
| 0:72–0:85 | Black card: **Read-only by design** · never flashes, erases or writes NVS/eFuses · chip info resets only with confirm · logs redacted · no network · MIT. `github.com/snackyear/hermes-esp32-debug`. | "esp32-debug for Hermes." |

Optional insert (0:14–0:20 alternative): "Which chip is this?" → Hermes asks before resetting → `esp32_chip_info(port=COM7,
confirm_reset=true)` → `ESP32-S3 (QFN56) (revision v0.2)`, 16MB flash, esptool 5.3.1 auto-detected from Arduino15.

## Expected tool output (recorded 2026-11-09)

`esp32_serial_capture`:
```json
{"ok":true,"port":"COM7","stop_reason":"panic_captured","redacted":true,"panic_detected":true,
 "panic_summary":{"chip_hint":"esp32s3","reason":"LoadProhibited","core":1,"elf_sha256_prefix":"2c9c09f13",
   "exception":"EXCCAUSE 28 = LoadProhibited","fault_address":"0x00000006","backtrace_frames":7},
 "next_step":"Panic found: call esp32_decode_backtrace with elf_path set to the build's .elf. log_text can be omitted: the decoder reuses this capture."}
```

`esp32_decode_backtrace` (frames abbreviated):
```text
elf_sha256_match: true   log_source: last_capture (COM7, 25 s ago)   addr2line: …\esp-x32\2601\bin\xtensa-esp32s3-elf-addr2line.exe
0x42008ed7  (anonymous namespace)::averageAccel(ImuFrame const*)  crash_demo.cpp:25   in_project
0x42008efa  (anonymous namespace)::processImuFrame()               crash_demo.cpp:32   in_project
0x42008f5f  (anonymous namespace)::triggerCrashDemo(char const*)   crash_demo.cpp:40   in_project
0x42008fb9  crashDemoPoll()                                        crash_demo.cpp:81     in_project
0x42008010  loop()                                                 StackChanAssistant-crashdemo.ino:1056
0x4203ede0  loopTask(void*)                                        cores\esp32\main.cpp:82  (framework)
0x4037f1ad  vPortTaskWrapper                                       freertos port.c:139      (framework)
hints: NULL (or NULL+offset) dereference in the first frame; core-dump errors are a missing partition, not the cause.
```
(Recorded with the timer trigger, so crashDemoPoll() is line 81; a screen tap gives line 85, a serial `crash` line 63.)

## Fallbacks
- **No human hand free?** Arm a timed crash first: `pwsh -File C:\GdriveM\ESP32\StackChanAssistant-crashdemo\arm_crash.ps1 -Seconds 40`,
  then ask Hermes to capture for 60 s. (The script opens COM7 with DTR/RTS released; a default pyserial open/close
  would reset the ESP32-S3 and cancel the timer.)
- **Live capture flaky?** `esp32_decode_backtrace` also accepts the pasted log (`log_text`) — say "recorded log" on screen.
- **Restore the original firmware:** `esptool --chip esp32s3 -p COM7 write-flash 0 C:\Users\plusl\Downloads\stackchan-COM7-backup-20261109.bin`
  (full 16 MB image, verified with `verify-flash`).
