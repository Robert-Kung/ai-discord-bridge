"""Cross-session recall (downsized): parent-session lineage frontmatter + the summary-dir
pointer injected into the combined system prompt, plus the trust boundary."""
import pytest

from bridge import config, memory, state


@pytest.fixture(autouse=True)
def _summaries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARIES_DIR", tmp_path / "sum")
    monkeypatch.setattr(config, "PROJECT_NOTES_DIR", tmp_path / "notes")
    # build_combined_system_prompt now writes the merged file under STATE_DIR/sysprompt
    # (was /tmp) so the executor can read it over the shared volume; keep it in tmp here.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")


# ── lineage frontmatter ─────────────────────────────────────────────────────
def test_frontmatter_written_when_sid_known():
    p = memory.save_summary(1, "the body", parent_session_id="sess-abc")
    text = p.read_text()
    assert text.startswith("---\n")
    assert "parent_session_id: sess-abc" in text
    # body round-trips: stripping frontmatter yields exactly the original content
    assert memory._strip_frontmatter(text) == "the body"


def test_no_frontmatter_when_sid_unknown():
    p = memory.save_summary(1, "plain body")
    text = p.read_text()
    assert not text.startswith("---")
    assert text == "plain body"
    # stripping a frontmatter-less summary is a no-op (backward compatible)
    assert memory._strip_frontmatter(text) == "plain body"


def test_strip_only_removes_real_frontmatter_not_a_leading_rule():
    # a body that merely OPENS with a --- line + has a later --- must NOT be stripped
    body = "---\n這是內文不是 frontmatter\n---\n更多內文"
    assert memory._strip_frontmatter(body) == body
    # empty + no-closing-fence are safe no-ops
    assert memory._strip_frontmatter("") == ""
    assert memory._strip_frontmatter("---\nkey: v\nno closing fence") == "---\nkey: v\nno closing fence"
    # a genuine frontmatter block IS stripped
    assert memory._strip_frontmatter("---\nparent_session_id: s\n---\nbody") == "body"


def test_same_second_flushes_do_not_clobber(monkeypatch):
    # recall relies on historical summaries accumulating — two flushes in the same wall
    # second must produce two distinct archive files, not overwrite.
    monkeypatch.setattr(memory.time, "strftime", lambda fmt: "20260707-120000")
    p1 = memory.save_summary(9, "first summary")
    p2 = memory.save_summary(9, "second summary")
    assert p1 != p2
    assert p1.read_text() == "first summary" and p2.read_text() == "second summary"
    # both archived + the pointer now sees ≥2 files
    d = memory.channel_summary_dir(9, config.DEFAULT_CWD)
    assert len(list(d.glob("2*.md"))) == 2


def test_injected_latest_summary_carries_no_frontmatter():
    memory.save_summary(1, "decided X", parent_session_id="sess-1")
    prompt_file = memory.build_combined_system_prompt(1, config.DEFAULT_CWD, "A")
    injected = prompt_file.read_text()
    assert "decided X" in injected
    assert "parent_session_id" not in injected  # frontmatter stripped before injection
    assert "---\nparent_session_id" not in injected


# ── recall pointer ──────────────────────────────────────────────────────────
def test_no_pointer_with_only_one_summary():
    memory.save_summary(2, "first")
    prompt_file = memory.build_combined_system_prompt(2, config.DEFAULT_CWD, "A")
    assert "跨 session" not in prompt_file.read_text()  # one flush → no older history


def test_pointer_present_when_older_summaries_exist():
    import time
    memory.save_summary(3, "older")
    time.sleep(1.05)  # timestamped filenames are second-resolution
    memory.save_summary(3, "newest")
    prompt_file = memory.build_combined_system_prompt(3, config.DEFAULT_CWD, "A")
    body = prompt_file.read_text()
    assert "歷史摘要（跨 session）" in body
    # the pointer names the correct (channel, cwd-slug) dir + the search instruction
    sdir = str(memory.channel_summary_dir(3, config.DEFAULT_CWD))
    assert sdir in body
    assert "Grep/Read" in body


# ── trust boundary (recall can only surface trust-filtered content) ─────────
def test_untrusted_buffer_content_never_reaches_a_summary(monkeypatch):
    # format_buffer_transcript is what feeds the buffer flush into a summary; it drops
    # anything _is_trusted rejects, so untrusted content can never be summarised → never
    # recalled, regardless of the retrieval mechanism.
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(state, "bot_user_ids", {"A": 1001})
    ch = 7
    state.channel_msg_log[ch].clear()
    state.channel_msg_log[ch].extend([
        {"id": 1, "author": "op", "author_id": 111, "bot": False,
         "content": "TRUSTED-DECISION", "ts": "2026-07-07T00:00:00", "cwd": config.DEFAULT_CWD},
        {"id": 2, "author": "rando", "author_id": 999, "bot": False,
         "content": "UNTRUSTED-INJECTION", "ts": "2026-07-07T00:00:01", "cwd": config.DEFAULT_CWD},
        {"id": 3, "author": "evilbot", "author_id": 5, "bot": True,
         "content": "THIRD-PARTY-BOT", "ts": "2026-07-07T00:00:02", "cwd": config.DEFAULT_CWD},
    ])
    transcript = memory.format_buffer_transcript(ch, cwd=config.DEFAULT_CWD)
    assert "TRUSTED-DECISION" in transcript
    assert "UNTRUSTED-INJECTION" not in transcript
    assert "THIRD-PARTY-BOT" not in transcript
