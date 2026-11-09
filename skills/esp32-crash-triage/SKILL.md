---
name: esp32-crash-triage
description: Step-by-step triage of an ESP32 / M5Stack / StackChan that crashes or keeps rebooting, using the read-only esp32-debug tools.
---

# ESP32 crash triage

Use when the user says a board "keeps rebooting", "crashes", shows "Guru Meditation", or a watchdog / brownout.

1. **Find the board.** `esp32_list_ports` (use `esp_only: true`). If several are ESP-like, ask which one.
2. **(Optional) Identify the chip.** `esp32_chip_info` resets the board — ask first, then pass `confirm_reset: true`.
   Skip it when the boot log already shows `ESP-ROM:esp32s3-...`.
3. **Capture.** `esp32_serial_capture` with `reset: true`, `seconds: 15`. On `port_busy`, ask the user to close
   Arduino Serial Monitor / `idf.py monitor`. If no panic appears, capture again for longer without reset.
4. **Decode.** `esp32_decode_backtrace` with the captured `log` and the `.elf` of the flashed build
   (Arduino IDE: *Sketch → Export Compiled Binary* or the temp build folder; PlatformIO: `.pio/build/<env>/firmware.elf`;
   ESP-IDF: `build/<project>.elf`). If `elf_sha256_match` is false, stop and ask for the right ELF.
5. **Explain.** Lead with `first_project_frame` (function + file:line), the exception, and the top hint.
   Propose a concrete code fix around the shown source lines. Treat hints as hypotheses.
6. **Boundary.** These tools never flash. Any fix is rebuilt and flashed by the user.
