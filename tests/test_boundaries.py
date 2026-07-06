"""Package-boundary guards (bridge-module-boundaries spec).

Two mechanical invariants the split must keep true, checked by AST over the whole
`bridge/` package so a future edit to any module can't silently break them:

1. Import allowlist — the execution-layer entry `execute`, the private chokepoint
   `_call_claude`, and the argv builder `build_claude_args` may only be imported
   where the design permits (`execute` → frontend only; the private symbols →
   nowhere outside the runner that defines them).
2. Config/state staleness — no module may `from bridge.config import <NAME>` a
   reloadable config global (or a shared state member); those must be attribute
   accesses so a `load_config()` reload is visible everywhere (else a consumer
   captures the pre-load default → silently-dead bot with a green canary).
"""
import ast
from pathlib import Path

from bridge import config

PKG = Path(config.__file__).resolve().parent
MODULES = {p.stem: p for p in PKG.glob("*.py") if p.stem != "__init__"}

# Reloadable names that must never be from-imported (captured by value).
_FORBIDDEN_FROM_IMPORTS = set(config._CONFIG_GLOBALS) | {
    # config members rebindable/mutated at runtime or in tests
    "STATE_DIR", "SUMMARIES_DIR", "PROJECT_NOTES_DIR",
    # shared mutable state members
    "pending_actions", "bot_user_ids", "clients", "session_ctx_tokens",
    "channel_msg_log", "messages_since_flush", "token_checkpointed",
}


def _from_imports(tree: ast.AST):
    """Yield (module, imported_name) for every `from X import name` in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                yield node.module, alias.name


def test_only_frontend_imports_execute_symbol():
    for stem, path in MODULES.items():
        if stem in ("runner", "frontend"):
            continue
        tree = ast.parse(path.read_text())
        for mod, name in _from_imports(tree):
            assert name != "execute", f"{stem}.py from-imports execute (execution-layer entry)"


def test_no_module_imports_private_chokepoint_symbols():
    for stem, path in MODULES.items():
        if stem == "runner":
            continue
        tree = ast.parse(path.read_text())
        for mod, name in _from_imports(tree):
            assert name not in ("_call_claude", "build_claude_args"), \
                f"{stem}.py from-imports the runner-private {name}"


def test_reloadable_config_names_are_never_from_imported():
    # from bridge.config import CHANNEL_ID  → captures the pre-load default forever.
    for stem, path in MODULES.items():
        tree = ast.parse(path.read_text())
        for mod, name in _from_imports(tree):
            if mod in ("bridge.config", "bridge.state") or mod in ("config", "state"):
                assert name not in _FORBIDDEN_FROM_IMPORTS, (
                    f"{stem}.py does `from {mod} import {name}` — use attribute access "
                    f"({mod.split('.')[-1]}.{name}) so a config reload is visible")
