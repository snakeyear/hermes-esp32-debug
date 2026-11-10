"""esp32-debug — read-only ESP32 debugging tools for Hermes Agent.

Registers four tools (toolset ``esp32_debug``):

* ``esp32_list_ports``       – enumerate serial ports, flag ESP32-like USB bridges
* ``esp32_chip_info``        – chip / MAC / flash size via esptool (subprocess; resets the board)
* ``esp32_serial_capture``   – bounded, redacted serial log capture
* ``esp32_decode_backtrace`` – Guru Meditation / abort backtrace -> function @ file:line

Plus a bundled skill, ``esp32-debug:esp32-crash-triage``. Nothing here flashes,
erases or writes to the device. See README.md for the full safety boundary.
"""

from pathlib import Path

TOOLSET = "esp32_debug"


def register(ctx):
    from .hermes_esp32_debug import schemas
    from .hermes_esp32_debug.tools import make_handlers

    def get_setting(key, default=None):
        getter = getattr(ctx, "get_config", None)
        if getter is None:
            return default
        return getter(key, default=default)

    cache_dir = None
    state = getattr(ctx, "state", None)
    data_dir = getattr(state, "data_dir", None) if state is not None else None
    if data_dir is not None:
        cache_dir = Path(data_dir)

    handlers = make_handlers(get_setting, cache_dir=cache_dir)
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=handlers[schema["name"]],
        )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir() and hasattr(ctx, "register_skill"):
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
