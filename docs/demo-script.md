# 90-second demo script — "My StackChan keeps rebooting"

Target length 85 s. Screen: Hermes (Desktop or CLI) on the left, the StackChan / M5 board on camera (picture-in-picture) on the right.
Board: a build you made yourself with its `.elf` (e.g. StackChanAssistant, or a spare M5 with a deliberate NULL-pointer sketch).
Before recording: close Arduino Serial Monitor; mask the taskbar clock/user name; use a throwaway Wi-Fi SSID.

| Time | Screen / action | Voice-over / caption |
| --- | --- | --- |
| 0:00–0:07 | Board on camera: face appears, freezes, screen goes black, reboots. Loop twice. | "My StackChan keeps rebooting. Let's ask Hermes." |
| 0:07–0:14 | Type: **"My StackChan keeps rebooting. Find out why — the ELF is at C:\…\StackChanAssistant\.pio\build\m5stack-cores3\firmware.elf"** | — |
| 0:14–0:22 | `esp32_list_ports` card → COM5 `303a:1001` *Espressif native USB*, confidence high. | "It finds the board by its USB ID — no port guessing." |
| 0:22–0:32 | Hermes asks "chip info resets the board, OK?" → you: "yes". `esp32_chip_info` → ESP32-S3 (QFN56) rev v0.2, 16MB flash, PSRAM 8MB. | "Chip info resets the board, so it asks first." |
| 0:32–0:45 | `esp32_serial_capture(reset=true)` → log scrolls; `Guru Meditation Error: Core 1 panic'ed (LoadProhibited)`; zoom on `wifi:connected with <redacted>` and `<ip>`. | "It captures the crash — and masks my Wi-Fi name, tokens and IPs before the model sees them." |
| 0:45–0:60 | `esp32_decode_backtrace` → `elf_sha256_match: true`; frames: **Face::draw(Avatar*) at src/face.cpp:57**, Avatar::update() 112, loop() 88; framework frames greyed; source snippet with `>   57 |` highlighted. | "The backtrace becomes file and line — and it checks the ELF really matches the firmware on the board." |
| 0:60–0:72 | Hermes' answer: "EXCVADDR 0x00000008 → NULL + offset. `avatar->face` is dereferenced before `Avatar::init()`. Fix: initialise in `setup()` before the first `update()`, or guard `if (!face) return;`" — show 3-line diff in the editor. | "Root cause and a concrete fix, in under a minute." |
| 0:72–0:85 | Black card, white text: **Read-only by design** · never flashes, erases or writes NVS/eFuses · chip info resets only with confirm · logs redacted · no network, no telemetry · MIT. Then: `hermes plugins install esp32-debug` + repo URL github.com/snackyear/hermes-esp32-debug. | "Read-only by design. esp32-debug for Hermes." |

Post: X post ≤ 90 s video, tag @NousResearch, link the catalog PR. Do not show real SSIDs, tokens, IPs or the MAC in full.

Fallback if the live crash is flaky: capture once with `reset=true` before recording, then replay with the saved log
(`esp32_decode_backtrace` accepts pasted text) — say so on screen ("recorded log").
