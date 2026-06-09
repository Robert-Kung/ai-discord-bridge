"""L1 — command/flag parsing + cwd slug. Pure string helpers; lock current
behaviour (incl. the known case-sensitivity of `!once`)."""
import bot


# ── parse_command ────────────────────────────────────────────────────────
def test_parse_command_basic():
    assert bot.parse_command("!mode plan") == ("mode", "plan")


def test_parse_command_no_args():
    assert bot.parse_command("!flush") == ("flush", "")


def test_parse_command_non_command():
    assert bot.parse_command("hello there") is None


def test_parse_command_lowercases_name():
    assert bot.parse_command("!HELP")[0] == "help"


def test_parse_command_bang_only():
    assert bot.parse_command("!") is None
    assert bot.parse_command("!   ") is None


def test_parse_command_collapses_whitespace():
    assert bot.parse_command("!cd    my-project") == ("cd", "my-project")


# ── extract_once_override ──────────────────────────────────────────────────
def test_once_valid_mode():
    cleaned, mode = bot.extract_once_override("do the thing !once bypass")
    assert mode == "bypass"
    assert cleaned == "do the thing"


def test_once_invalid_mode_not_swallowed():
    cleaned, mode = bot.extract_once_override("hello !once frobnicate")
    assert mode is None
    assert cleaned == "hello !once frobnicate"


def test_once_absent():
    assert bot.extract_once_override("plain message") == ("plain message", None)


def test_once_no_mode_after():
    cleaned, mode = bot.extract_once_override("text !once")
    assert mode is None


def test_once_is_case_sensitive_known_limitation():
    # `!once` matcher is literal/case-sensitive; uppercase is NOT recognised.
    # Locked as current behaviour, not a feature.
    cleaned, mode = bot.extract_once_override("do X !ONCE bypass")
    assert mode is None


# ── extract_yolo_flag ──────────────────────────────────────────────────────
def test_yolo_present():
    cleaned, yolo = bot.extract_yolo_flag("!yolo ship it")
    assert yolo is True
    assert cleaned == "ship it"


def test_yolo_uppercase():
    _, yolo = bot.extract_yolo_flag("!YOLO go")
    assert yolo is True


def test_yolo_absent():
    assert bot.extract_yolo_flag("just talk") == ("just talk", False)


# ── _cwd_slug ──────────────────────────────────────────────────────────────
def test_cwd_slug_stable():
    assert bot._cwd_slug("/home/user/proj") == bot._cwd_slug("/home/user/proj")


def test_cwd_slug_distinct_per_dir():
    # session isolation depends on different cwds producing different slugs
    assert bot._cwd_slug("/home/user") != bot._cwd_slug("/home/user/proj")
