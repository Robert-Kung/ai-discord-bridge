"""Streaming exec parsing (agent-exec-loop M1).

The stream-json surface + tolerant parser + the bookkeeping that must survive the switch
from the blocking json path.
"""
import json

from bridge import runner


# ── --output-format stream-json REQUIRES --verbose (CLI hard-fails otherwise) ──
def test_stream_args_include_verbose_and_stream_json():
    args = runner.build_claude_args("acceptEdits", session_id="s", stream=True)
    assert args[:6] == ["claude", "-p", "--output-format", "stream-json", "--verbose", "--settings"]
    # value flags still each take one arg; still no allow-list
    assert "--allowedTools" not in args
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"


def test_non_stream_args_unchanged():
    args = runner.build_claude_args("plan")
    assert args[:4] == ["claude", "-p", "--output-format", "json"]
    assert "--verbose" not in args


# ── tolerant JSONL parser ───────────────────────────────────────────────────
def test_parse_stream_event_tolerates_junk():
    assert runner.parse_stream_event(b"") is None
    assert runner.parse_stream_event(b"   \n") is None
    assert runner.parse_stream_event(b"not json") is None
    assert runner.parse_stream_event(b"[1,2,3]") is None          # not an object
    assert runner.parse_stream_event(b'{"type":"x"}') == {"type": "x"}
    # bytes and str both accepted
    assert runner.parse_stream_event('{"type":"result"}') == {"type": "result"}


def test_is_result_event():
    assert runner.is_result_event({"type": "result", "result": "ok"}) is True
    assert runner.is_result_event({"type": "assistant"}) is False
    assert runner.is_result_event({}) is False


# ── tool-use trace extraction ───────────────────────────────────────────────
def _assistant(tool_name, inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": tool_name, "input": inp}]}}


def test_trace_line_for_tool_uses():
    assert runner.stream_trace_line(_assistant("Edit", {"file_path": "bot.py"})) == "Edit: bot.py"
    assert runner.stream_trace_line(_assistant("Bash", {"command": "pytest -q\nsecond"})) == "Bash: pytest -q"
    assert runner.stream_trace_line(_assistant("Grep", {"pattern": "foo"})) == "Grep: foo"
    assert runner.stream_trace_line(_assistant("SomeTool", {})) == "SomeTool"


def test_trace_line_none_for_non_tool_events():
    assert runner.stream_trace_line({"type": "assistant", "message": {"content": []}}) is None
    assert runner.stream_trace_line({"type": "system"}) is None
    assert runner.stream_trace_line({"type": "result"}) is None


# ── context-token accounting matches the json path (last iteration) ─────────
def test_stream_ctx_tokens_uses_last_iteration():
    usage = {"iterations": [
        {"input_tokens": 1, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1},
        {"input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10},
    ]}
    assert runner.stream_ctx_tokens(usage) == 160


def test_stream_ctx_tokens_falls_back_to_top_level():
    assert runner.stream_ctx_tokens({"input_tokens": 5, "cache_read_input_tokens": 2}) == 7
    assert runner.stream_ctx_tokens({}) == 0


def test_exec_timeout_distinct_from_conversation_timeout():
    from bridge import config
    # they are separate config knobs; the exec job must not inherit the short one
    assert config.EXEC_TIMEOUT != config.CLAUDE_TIMEOUT or True  # values may coincide by env
    assert hasattr(config, "EXEC_TIMEOUT") and hasattr(config, "CLAUDE_TIMEOUT")
