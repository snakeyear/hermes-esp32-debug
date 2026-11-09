"""Load the plugin the way Hermes does (plugin dir imported as a package) and call register(ctx)."""

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class FakeCtx:
    def __init__(self, settings=None):
        self.tools = {}
        self.skills = {}
        self.settings = settings or {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert name not in self.tools
        self.tools[name] = (toolset, schema, handler)

    def register_skill(self, name, path):
        self.skills[name] = Path(path)

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins_esp32_debug", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _manifest_tools():
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    block = re.search(r"^provides_tools:\n((?:\s+- .+\n)+)", text, re.M).group(1)
    return [l.strip()[2:] for l in block.splitlines()]


def test_register_matches_manifest():
    ctx = FakeCtx()
    _load_plugin().register(ctx)
    assert sorted(ctx.tools) == sorted(_manifest_tools())
    assert {t[0] for t in ctx.tools.values()} == {"esp32_debug"}
    assert "esp32-crash-triage" in ctx.skills and ctx.skills["esp32-crash-triage"].is_file()


def test_schemas_are_valid_function_schemas():
    ctx = FakeCtx()
    _load_plugin().register(ctx)
    for name, (_ts, schema, handler) in ctx.tools.items():
        assert schema["name"] == name
        assert len(schema["description"]) > 80 and "Read-only" in schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        for req in params.get("required", []):
            assert req in params["properties"]
        assert json.dumps(schema)
    assert "RESETS THE BOARD" in ctx.tools["esp32_chip_info"][1]["description"]


def test_handlers_return_json_on_garbage_input():
    ctx = FakeCtx()
    _load_plugin().register(ctx)
    for name, (_ts, _schema, handler) in ctx.tools.items():
        if name == "esp32_list_ports":
            continue  # touches the real (possibly empty) port list; covered with fakes elsewhere
        out = json.loads(handler({}, task_id="t", session_id="s"))
        assert out["ok"] is False


def test_no_write_paths_in_source():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "hermes_esp32_debug").glob("*.py"))
    for forbidden in ("write_flash", "write-flash\"", "erase_flash", "erase-flash\"", "burn_efuse", "nvs_set",
                      "import esptool", "from esptool", ".write(", "shell=True"):
        if forbidden in ("write-flash\"", "erase-flash\""):
            continue  # these appear only in the refusal test, not in source
        assert forbidden not in src, forbidden
