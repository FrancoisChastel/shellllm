# shellllm

[![ci](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml/badge.svg)](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Local-LLM zsh helpers.

- **`, <english>`** — proposes 3–5 shell commands with one-line notes, you pick one in `fzf`, it lands on your prompt line via `print -z`. Never auto-executes.
- **`? <question>`** — small read-only agent with three tools: `read_file` (gated by a filesystem hard wall), `web_search` (DuckDuckGo) and `fetch_url` (follow a result into its page, plain-text). Searches only when the model decides it needs to. Answer streams as live-rendered markdown.
- **`??? <question>`** — same agent, web-first: always starts with a `web_search` and follows the best link with `fetch_url`. Use it when you want fresh facts, not the model's prior.
- **`??`** — start (or stop / list / status) the local `llama-server` backend, with named tiers for speed-vs-quality.

Runs against a local `llama-server`. No frontier model, no API key, works with wifi off.

## Quick start

### Install with Homebrew (recommended)

```sh
brew install FrancoisChastel/shellllm/shellllm
echo 'source "$(brew --prefix)/share/shellllm/shellllm.zsh"' >> ~/.zshrc
exec zsh
```

That pulls `llama.cpp`, `fzf`, and the two CLIs (`shellllm-comma`, `shellllm-ask`). See [docs/HOMEBREW.md](docs/HOMEBREW.md) for the maintainer release flow.

### Install from source

```sh
# 1. install
python3 -m venv .venv
.venv/bin/pip install -e .

# 2. wire zsh
echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> ~/.zshrc
echo "source $PWD/zsh/shellllm.zsh"          >> ~/.zshrc
exec zsh

# 3. start the backend (downloads not handled here — see "Models" below)
??               # default tier (balanced)
?? --start fast  # MoE + MTP, fastest
?? --list        # what's available locally vs. needs download

# 4. use it
, find the five largest files under this directory
? in markdown, what does git stash do?
??? latest stable release of ripgrep and one notable change in it
```

## Tiers

| Tier | Model | Notes |
| --- | --- | --- |
| `fast` | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` | MoE with 3B active params + MTP self-speculative decoding (`--spec-type draft-mtp`). Fastest on Apple Silicon. |
| `balanced` | `unsloth/Qwen3.6-27B-GGUF` (Q4_K_M) | Dense 27B. Default. |
| `smart` | `unsloth/Qwen3-Coder-Next-GGUF` | Latest coder-tuned model, ideal for shell/agent tasks. |

Download a tier:

```sh
huggingface-cli download unsloth/Qwen3-Coder-Next-GGUF
?? --start smart
```

`??` resolves the GGUF inside your HuggingFace cache automatically — no path config required.

## Architecture

```
src/shellllm/
├── safe_fs.py    filesystem hard wall — $HOME/$PWD + inside-HOME denylist
├── client.py     llama-server HTTP client (one-shot + streaming)
├── comma.py      ,    — JSON-schema → fzf picker → stdout
├── ask.py        ?    — streaming agent loop, live markdown render
├── search.py     ???  — same loop, web-search-first system prompt
└── web.py        stdlib DuckDuckGo scraper + fetch_url with SSRF guard
tests/test_safe_fs.py   filesystem-wall coverage
tests/test_web.py       URL safety + HTML extraction
zsh/shellllm.zsh        function ,  + aliases ? , ?? , ???
.github/workflows/ci.yml  ruff + pytest on push & PR
```

## The hard wall

Every file read goes through `safe_fs.safe_read`. Four rules, all enforced:

1. **Canonicalize** with `.resolve(strict=True)`. Symlinks and `..` are flattened *before* containment is checked.
2. **Contain** to `$HOME` or `$PWD`. Anywhere else refuses with `WallViolation`.
3. **Deny inside-HOME secrets.** Even within `$HOME`, paths under `.ssh`, `.aws`, `.gnupg`, `.kube`, `Library/Keychains`, `.netrc`, etc. refuse. Match is by path component — `.sshfoo` is allowed.
4. **Regular files only.** Devices, fifos, sockets, directories refuse.

Reads cap at 1 MB and use `O_NOFOLLOW` on the final component as a belt against a resolve-then-open symlink race.

```sh
pytest -v   # 38 tests covering symlinks, traversal, denylist, lookalikes, truncation
```

## What's deliberately not built

- **GBNF prefix grammar** for `,`. JSON schema is enough for v1; the system prompt forbids the obvious destructive commands.
- **JavaScript rendering** for `fetch_url`. Pages are fetched as static HTML and reduced to text — SPAs that need JS to populate content will look empty.

## Tunables (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHELLLM_BASE_URL` | `http://127.0.0.1:8080` | llama-server endpoint |
| `SHELLLM_LLAMA_MODEL` | — | explicit GGUF path, overrides tier |
| `SHELLLM_PORT` | `8080` | server port |
| `SHELLLM_NGL` | `99` | GPU offload layers |
| `SHELLLM_CTX` | `8192` | context window |
| `SHELLLM_LOG` | `~/.cache/shellllm/llama-server.log` | server log path |
| `SHELLLM_TIMEOUT` | `120` | HTTP timeout (seconds) |

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -v
```

## License

[MIT](LICENSE)
