# AGENTS.md

Conventions for AI coding assistants (Claude Code, Cursor, Aider, etc.) working in this repo. Humans contributing should read this too — it captures the project's philosophy in a few hundred words.

## Quickstart for contributors

```sh
git clone https://github.com/FrancoisChastel/shellllm && cd shellllm
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                                  # 257 tests, < 1s
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

End-user docs: [README.md](README.md). Deep config: [CONFIGURATION.md](CONFIGURATION.md). Homebrew packaging + release flow: [docs/HOMEBREW.md](docs/HOMEBREW.md).

## What this project is

`shellllm` exposes four glyph commands to zsh, backed by a local `llama-server`:

- `,` proposes shell commands; never executes. No terminal context.
- `,,` the same with terminal context; bare `,,` fixes the previous command. Still never executes.
- `?` answers questions through a narrow read-only agent. Pipe-friendly (`cmd | ? …`).
- `??` starts/stops/lists the backend; tiers bind to ports and can serve side by side.
- `???` memory & recall: bare query searches archived sessions, flags pin long-term facts.

Three things matter more than anything else: **the filesystem hard wall**, the **comma never executes** invariant, and the **terminal-context ladder** (`shell_context.py`: capture is gated by `SHELLLM_SHELL_CONTEXT`, redacted, per-turn ephemeral, never persisted). Everything else is negotiable.

## The hard wall

`src/shellllm/safe_fs.py` is the only path that touches the disk on behalf of the LLM. If you add a feature that reads files, route it through `safe_resolve` / `safe_read` — never `open()` or `Path.read_text()` directly.

- Containment rule: $HOME or $PWD, nowhere else.
- Inside-HOME denylist: `.ssh`, `.aws`, `.gnupg`, `.kube`, `Library/Keychains`, etc.
- Only regular files (no devices, fifos, sockets, directories).
- Reads capped at `MAX_READ_BYTES`.

If you change `safe_fs.py`, the tests in `tests/test_safe_fs.py` are not optional. **All 38 must pass.** New behavior gets a new test. New denylist entries get a new parametrize case.

## What's deliberately not built (yet)

- Anything that lets `?` modify the filesystem, run shell commands, or follow URLs returned by `web_search`. The "read-only over the network" property is load-bearing.

## Code style

- **PEP 8** + type annotations on every function signature.
- **ruff** is the source of truth for lint and format. `ruff check . && ruff format --check .` must be clean.
- Immutable data structures unless mutation is essential.
- Files stay under ~400 lines. Functions stay under ~50.
- Use the existing `safe_fs` / `client` / `web` modules; don't reinvent.
- Errors at system boundaries (HTTP, filesystem, subprocess) surface as exceptions with a one-line "what to do" hint when they're user-visible.

## Tests

```sh
pytest -v                   # all tests
pytest tests/test_safe_fs.py -v   # wall only (run this any time you touch safe_fs.py)
```

Wall tests use `tmp_path` + `monkeypatch.setenv("HOME", ...)` + `monkeypatch.chdir(...)`. Don't hit the real filesystem.

## Adding a new tool to `?`

1. Implement the function in `src/shellllm/tools_*.py` (one file per tool family, or extend `ask.py` directly if trivial). It must return `str`.
2. If it touches files, use `safe_fs`.
3. Register the OpenAI-shaped schema in `ask.TOOLS`.
4. Wire dispatch in `ask._dispatch`.
5. Update `ask.SYSTEM` to mention the new tool's contract.
6. Add an integration smoke test where possible.

## Adding a new tier to `??`

Edit `zsh/shellllm.zsh`. Append to all three associative arrays:

```zsh
_SHELLLM_TIER_REPO[<name>]="huggingface/repo"
_SHELLLM_TIER_ARGS[<name>]="--extra llama-server flags"
_SHELLLM_TIER_DESC[<name>]="one-line description"
```

And add `<name>` to `_SHELLLM_TIER_ORDER`. That's it — `??` picks up the new tier automatically.

## Commits

- Conventional commit style: `feat: …`, `fix: …`, `refactor: …`, `docs: …`, `test: …`, `chore: …`.
- One concern per commit. If a PR fixes a bug and refactors the wall, that's two commits.
- Run `ruff check . && pytest -v` before committing.

## Things to *not* do

- Don't add `__pycache__/`, `.venv/`, or `*.gguf` to the repo (they're in `.gitignore`).
- Don't add dependencies casually. Current deps: `httpx`, `rich`. Adding one is a discussion.
- Don't bypass `safe_fs` "just for this one case."
- Don't make `,` execute. Don't make `?` write.
- Don't add telemetry. This project never phones home.

## Where to look

- Code: `src/shellllm/`
- Tests: `tests/`
- Shell glue: `zsh/shellllm.zsh`
- CI: `.github/workflows/ci.yml`

If you're an AI assistant, read `safe_fs.py` and `tests/test_safe_fs.py` first. Everything else makes more sense once you've seen the wall.
