"""L1 — command/flag parsing + cwd slug. Pure string helpers; lock current
behaviour (incl. the known case-sensitivity of `!once`)."""
from bridge import frontend, sessions


# ── parse_command ────────────────────────────────────────────────────────
def test_parse_command_basic():
    assert frontend.parse_command("!mode plan") == ("mode", "plan")


def test_parse_command_no_args():
    assert frontend.parse_command("!flush") == ("flush", "")


def test_parse_command_non_command():
    assert frontend.parse_command("hello there") is None


def test_parse_command_lowercases_name():
    assert frontend.parse_command("!HELP")[0] == "help"


def test_parse_command_bang_only():
    assert frontend.parse_command("!") is None
    assert frontend.parse_command("!   ") is None


def test_parse_command_collapses_whitespace():
    assert frontend.parse_command("!cd    my-project") == ("cd", "my-project")


# ── extract_once_override ──────────────────────────────────────────────────
def test_once_valid_mode():
    cleaned, mode = frontend.extract_once_override("do the thing !once bypass")
    assert mode == "bypass"
    assert cleaned == "do the thing"


def test_once_invalid_mode_not_swallowed():
    cleaned, mode = frontend.extract_once_override("hello !once frobnicate")
    assert mode is None
    assert cleaned == "hello !once frobnicate"


def test_once_absent():
    assert frontend.extract_once_override("plain message") == ("plain message", None)


def test_once_no_mode_after():
    cleaned, mode = frontend.extract_once_override("text !once")
    assert mode is None


def test_once_is_case_sensitive_known_limitation():
    cleaned, mode = frontend.extract_once_override("do X !ONCE bypass")
    assert mode is None


# ── extract_yolo_flag ──────────────────────────────────────────────────────
def test_yolo_present():
    cleaned, yolo = frontend.extract_yolo_flag("!yolo ship it")
    assert yolo is True
    assert cleaned == "ship it"


def test_yolo_uppercase():
    _, yolo = frontend.extract_yolo_flag("!YOLO go")
    assert yolo is True


def test_yolo_absent():
    assert frontend.extract_yolo_flag("just talk") == ("just talk", False)


# ── _cwd_slug ──────────────────────────────────────────────────────────────
def test_cwd_slug_stable():
    assert sessions._cwd_slug("/home/user/proj") == sessions._cwd_slug("/home/user/proj")


def test_cwd_slug_distinct_per_dir():
    assert sessions._cwd_slug("/home/user") != sessions._cwd_slug("/home/user/proj")
