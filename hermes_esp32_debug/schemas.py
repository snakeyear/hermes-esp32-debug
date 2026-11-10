"""Tool schemas — what the model reads to decide when and how to call each tool."""

SAFETY = (
    " Read-only: this plugin never flashes, erases, writes NVS/eFuses or sends data to the board."
)

LIST_PORTS = {
    "name": "esp32_list_ports",
    "description": (
        "List the serial (COM) ports on this computer with USB VID:PID, description and "
        "manufacturer, and flag the ones that look like an ESP32 / M5Stack / StackChan board "
        "(Espressif native USB 303a:*, CP210x 10c4:ea60, CH34x 1a86:*, FTDI). Opens no port and "
        "does not touch any device. Call this first when debugging an ESP32 to find its port." + SAFETY
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "esp_only": {
                "type": "boolean",
                "description": "Only return ports that look like an ESP32 board. Default false.",
            }
        },
        "required": [],
    },
}

CHIP_INFO = {
    "name": "esp32_chip_info",
    "description": (
        "Identify the ESP32 on a serial port: chip type and revision, features, crystal, MAC "
        "address and flash size, by running esptool's read-only 'flash-id' (and 'chip-id' if "
        "needed) as a subprocess (standalone esptool.exe from Arduino/PlatformIO or python -m esptool). WARNING: this RESETS THE BOARD into the ROM bootloader and "
        "back — the running firmware restarts and any conversation or state on the device is "
        "lost, and the port must not be open in another program. Ask the user before calling "
        "unless they already asked for chip info. You must pass confirm_reset=true." + SAFETY
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "port": {"type": "string", "description": "Serial port, e.g. 'COM5' or '/dev/ttyACM0'."},
            "confirm_reset": {
                "type": "boolean",
                "description": "Must be true: acknowledges the board will be reset.",
            },
            "baud": {"type": "integer", "description": "esptool baud rate. Default 115200."},
        },
        "required": ["port", "confirm_reset"],
    },
}

SERIAL_CAPTURE = {
    "name": "esp32_serial_capture",
    "description": (
        "Capture a bounded slice of serial log output from an ESP32 (boot log, ESP_LOGx output, "
        "Guru Meditation / panic backtraces). Stops at the time limit, the line limit, or ~1.5 s "
        "after a panic backtrace is seen. Secrets (Wi-Fi SSID/password, tokens, keys) and IP "
        "addresses are masked before the log is returned. Fails fast with error 'port_busy' if "
        "another program (Arduino Serial Monitor, idf.py monitor) holds the port. Optionally "
        "resets the board via RTS first so the boot log and an early crash are captured; "
        "without reset, nothing is sent to the board." + SAFETY
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "port": {"type": "string", "description": "Serial port, e.g. 'COM5'."},
            "baud": {"type": "integer", "description": "Baud rate. Default 115200."},
            "seconds": {"type": "number", "description": "How long to listen, 0.5-60 s. Default 10."},
            "max_lines": {"type": "integer", "description": "Maximum lines to return, 1-2000. Default 300."},
            "reset": {
                "type": "boolean",
                "description": "Pulse RTS to reboot the board into its app before capturing. Default false.",
            },
            "stop_on_panic": {
                "type": "boolean",
                "description": "Stop shortly after a panic backtrace is captured. Default true.",
            },
        },
        "required": ["port"],
    },
}

DECODE_BACKTRACE = {
    "name": "esp32_decode_backtrace",
    "description": (
        "Decode an ESP32 crash (Guru Meditation, abort(), assert, watchdog, stack overflow) to "
        "function and source file:line. Parses Xtensa 'Backtrace: 0xPC:0xSP ...' (ESP32/S2/S3) "
        "and RISC-V MEPC/RA registers (ESP32-C3/C6/H2/P4), runs the Espressif toolchain's "
        "addr2line on the given ELF, decodes EXCCAUSE/MCAUSE, checks the 'ELF file SHA256' in "
        "the log against the ELF so a wrong build is flagged, and returns heuristic hints plus a "
        "short source snippet around each frame when the source is on this machine. The ELF "
        "must be the exact build flashed on the board (the .elf, not the .bin)." + SAFETY
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "log_text": {
                "type": "string",
                "description": ("Serial output containing the panic (at most 256 KiB, the same cap as a capture). "
                                "Omit it (or pass 'last') to reuse the log of the most recent esp32_serial_capture, "
                                "which avoids re-typing addresses."),
            },
            "elf_path": {"type": "string", "description": "Path to the firmware .elf for the flashed build."},
            "chip": {
                "type": "string",
                "description": "Chip, e.g. 'esp32s3', 'esp32', 'esp32c3'. Optional; inferred from the log when possible.",
            },
            "which_panic": {
                "type": "string",
                "enum": ["last", "first"],
                "description": "Which panic to decode when the log has several. Default 'last'.",
            },
            "include_source": {
                "type": "boolean",
                "description": "Include a few source lines around each resolved frame. Default true.",
            },
        },
        "required": ["elf_path"],
    },
}

ALL = [LIST_PORTS, CHIP_INFO, SERIAL_CAPTURE, DECODE_BACKTRACE]
