"""Tests for the opt-in terminal-context block (shellllm.shell_context)."""

from __future__ import annotations

from shellllm.shell_context import (
    MAX_PIPED_CHARS,
    build_piped_block,
    build_shell_context_block,
    redact,
)

ENV_FULL = {
    "SHELLLM_SHELL_CONTEXT": "output",
    "SHELLLM_LAST_CMD": "git push origin main",
    "SHELLLM_LAST_STATUS": "1",
    "SHELLLM_RECENT_HISTORY": "git status\ngit add .\ngit commit -m wip",
    "SHELLLM_PANE_OUTPUT": "error: failed to push some refs to 'origin'",
}


# ─── ladder levels ──────────────────────────────────────────────────────


def test_off_by_default():
    assert build_shell_context_block(env={}) == ""


def test_off_even_when_vars_are_set():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="off")
    assert build_shell_context_block(env=env) == ""


def test_unknown_level_fails_safe_to_off():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="everything")
    assert build_shell_context_block(env=env) == ""


def test_cmd_level_includes_last_command_and_status():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="cmd")
    out = build_shell_context_block(env=env)
    assert "git push origin main" in out
    assert "1" in out


def test_cmd_level_excludes_history_and_output():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="cmd")
    out = build_shell_context_block(env=env)
    assert "git commit -m wip" not in out
    assert "failed to push some refs" not in out


def test_history_level_includes_recent_commands():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="history")
    out = build_shell_context_block(env=env)
    assert "git commit -m wip" in out
    assert "failed to push some refs" not in out


def test_output_level_includes_pane_output():
    out = build_shell_context_block(env=ENV_FULL)
    assert "failed to push some refs" in out


def test_failed_status_is_annotated():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="cmd")
    out = build_shell_context_block(env=env)
    assert "failed" in out.lower()


def test_zero_status_not_annotated_as_failed():
    env = dict(ENV_FULL, SHELLLM_SHELL_CONTEXT="cmd", SHELLLM_LAST_STATUS="0")
    out = build_shell_context_block(env=env)
    assert "failed" not in out.lower()


def test_empty_when_level_on_but_no_data():
    env = {"SHELLLM_SHELL_CONTEXT": "cmd"}
    assert build_shell_context_block(env=env) == ""


# ─── caps ───────────────────────────────────────────────────────────────


def test_long_output_is_tail_truncated():
    env = dict(ENV_FULL, SHELLLM_PANE_OUTPUT="x" * 10_000 + "TAIL_MARKER")
    out = build_shell_context_block(env=env)
    assert "TAIL_MARKER" in out
    assert len(out) < 6_000


def test_history_capped_to_recent_lines():
    lines = [f"cmd-{i}" for i in range(50)]
    env = dict(
        ENV_FULL,
        SHELLLM_SHELL_CONTEXT="history",
        SHELLLM_RECENT_HISTORY="\n".join(lines),
    )
    out = build_shell_context_block(env=env)
    assert "cmd-49" in out  # most recent kept
    assert "cmd-0" not in out  # oldest dropped


# ─── redaction ──────────────────────────────────────────────────────────


def test_redacts_keyed_assignment():
    out = redact("export OPENAI_API_KEY=sk-abc123def456ghi789jkl")
    assert "sk-abc123def456ghi789jkl" not in out
    assert "[redacted]" in out


def test_redacts_password_colon_form():
    out = redact("password: hunter2hunter2")
    assert "hunter2hunter2" not in out


def test_redacts_bearer_header():
    out = redact("curl -H 'Authorization: Bearer abc.def.ghi'")
    assert "abc.def.ghi" not in out


def test_redacts_aws_access_key():
    out = redact("AKIAIOSFODNN7EXAMPLE was leaked")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_redacts_github_token():
    out = redact(
        "git remote set-url origin https://ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/x/y"
    )
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in out


def test_redacts_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    out = redact(f"token is {jwt}")
    assert jwt not in out


def test_keeps_git_sha():
    sha = "3bb70645a2b9e7c1d8f4a6b3c9d2e1f0a7b8c9d0"
    out = redact(f"git checkout {sha}")
    assert sha in out


def test_block_is_redacted():
    env = dict(ENV_FULL, SHELLLM_LAST_CMD="export STRIPE_SECRET=sk_live_abcdef123456")
    out = build_shell_context_block(env=env)
    assert "sk_live_abcdef123456" not in out


# ─── piped input ────────────────────────────────────────────────────────


def test_piped_block_empty_for_blank_input():
    assert build_piped_block("") == ""
    assert build_piped_block("   \n  ") == ""


def test_piped_block_wraps_content():
    out = build_piped_block("error: linker `cc` not found")
    assert "Piped input" in out
    assert "linker `cc` not found" in out


def test_piped_block_is_redacted():
    out = build_piped_block("Authorization: Bearer super.secret.token1234")
    assert "super.secret.token1234" not in out


def test_piped_block_keeps_tail_when_oversized():
    text = "x" * (MAX_PIPED_CHARS + 100) + "FINAL_ERROR"
    out = build_piped_block(text)
    assert "FINAL_ERROR" in out
    assert "truncated" in out
