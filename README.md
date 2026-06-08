# shellllm

[![ci](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml/badge.svg)](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Local-LLM zsh helpers.

- **`, <english>`** — proposes 3–5 shell commands with one-line notes, you pick one in `fzf`, it lands on your prompt line via `print -z`. Never auto-executes. Sticky per-pane session so follow-ups refine the prior list (`, the same but only the running ones`).
- **`? <question>`** — small read-only agent with three tools: `read_file` (gated by a filesystem hard wall), `web_search` (DuckDuckGo) and `fetch_url` (follow a result into its page, plain-text). Searches only when the model decides it needs to. Answer streams as live-rendered markdown. Each terminal pane keeps its own sticky conversation — follow-ups continue automatically until 30 min of idle (or `? --new`).
- **`??? <question>`** — same agent, web-first: always starts with a `web_search` and follows the best link with `fetch_url`. Use it when you want fresh facts, not the model's prior. Has its own per-pane session, distinct from `?`.
- **`?: <subcommand>`** — long-term facts and cross-session recall. `?: add <fact>`, `?: list`, `?: drop <n>`, `?: recall <query>`, `?: status`. Lives outside the asking commands so `?` and `,` stay ask-only.
- **`??`** — start (or stop / list / status) the local `llama-server` backend, with named tiers for speed-vs-quality. `?? --start-embed` boots a second `llama-server` in embedding mode for hybrid semantic recall.

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
, the same but only ones modified today        # refines the prior , — sticky session
? in markdown, what does git stash do?
?: add I prefer ripgrep over grep             # long-term fact, used by all asks
?: recall ripgrep                              # search past sessions across panes
??? latest stable release of ripgrep and one notable change in it
```

### Upgrading an existing install

Both code paths track this repo as the source of truth:

- **Source install (`pip install -e .` + `source zsh/shellllm.zsh`)** —
  Python entry-points pick up edits automatically; the zsh helper does
  too. After a `git pull` that changes `zsh/shellllm.zsh` you only need
  to re-source it:
  ```sh
  exec zsh
  ```
- **Homebrew install** — bump the formula or wait for the next release.

To activate semantic recall once the upgrade is in, follow the
[Local embeddings](#local-embeddings) section.

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

## Sessions

Each terminal pane gets its own sticky conversation, one per asking
command. `?`, `???`, and `,` each keep their own thread so a refining
`,` doesn't pollute the Q&A you were having with `?`.

```sh
? what was that flag for ripgrep again
? and how do I use it with json output
? --history                # transcript of this pane's session
? --new what's a good hash for cache keys     # start fresh
? --reset                  # drop the current session
? --compact                # force-compact older turns into a summary

, list all docker containers
, the same but only the running ones    # refines the prior `,` proposal
, --new find the largest files          # starts a fresh `,` thread
```

The pane is identified from `TERM_SESSION_ID` (Terminal.app / iTerm),
`TMUX_PANE`, or `WINDOWID` — whichever your terminal sets. After 30 min
idle the session auto-rotates so a forgotten tab doesn't bleed stale
context into the next turn.

When the conversation crosses ~80% of `SHELLLM_CTX`, older turns are
auto-summarized into a single `<summary-so-far>` block using the same
local model; the most recent 4 turns stay verbatim.

## `?:` — facts and recall

Everything that *isn't* "ask a question" or "propose a command" lives
under one meta verb so `?` and `,` stay clean. `?:` is your durable
layer.

```sh
?: add I prefer ripgrep over grep    # pin a long-term fact
?: list                              # see them
?: drop 2                            # remove fact #2
?: recall ripgrep                    # search archived sessions
?: status                            # counts: facts + archives
?: help
```

Long-term facts get injected at the top of every `?` / `???` system
prompt, so the agent stops asking you things you've already told it.
Recall works against `~/.cache/shellllm/archive.db`, populated
automatically whenever a session expires or you call `--new` / `--reset`
on any asking command.

Recall always works in **BM25-only mode** — no extra setup, no extra
processes. Adding a local embedding server unlocks **hybrid
semantic + BM25 search** (RRF-fused) so you find prior conversations
by meaning, not just keywords.

<a id="local-embeddings"></a>

### Local embeddings

Start a second `llama-server` instance running in embedding mode on
port 8081. Three tiers ship out of the box:

| Tier | Model | Notes |
| --- | --- | --- |
| `tiny` | `Qwen/Qwen3-Embedding-0.6B-GGUF` | Same family as the chat tiers (default). |
| `bge` | `ChristianAzinn/bge-small-en-v1.5-gguf` | Tiny English-only, very fast. |
| `nomic` | `nomic-ai/nomic-embed-text-v1.5-GGUF` | Strong general-purpose retrieval. |

```sh
huggingface-cli download Qwen/Qwen3-Embedding-0.6B-GGUF
?? --start-embed tiny       # starts on :8081
?? --status-embed
?? --list-embed
?? --stop-embed
```

shellllm auto-detects the server via `SHELLLM_EMBED_URL`
(`http://127.0.0.1:8081` by default). When it's reachable:

- new archive rows get a normalized fp32 embedding written alongside
  the FTS5 entry;
- `?: recall` and `? --auto-recall` embed the query and add cosine-sim
  candidates to the BM25 results, fused via Reciprocal Rank Fusion;
- mismatched embedding dims (e.g. swapping the model later) are
  silently skipped — old rows still serve BM25 hits.

Auto-recall is opt-in:

```sh
export SHELLLM_AUTO_RECALL=1    # global on
? --no-auto-recall <q>          # off for one call
? --auto-recall <q>             # on for one call (overrides env)
```

## Optional: cross-session memory via claude-mem

If you also use [claude-mem](https://github.com/thedotmack/claude-mem)
in its server-beta mode, shellllm can write each turn as an
observation and pull in relevant prior context on a fresh session.
Nothing else changes; if the env vars aren't set, the integration is
inert.

```sh
export CLAUDE_MEM_SERVER_BETA_URL="https://your-claude-mem-host"
export CLAUDE_MEM_SERVER_BETA_API_KEY="..."
export CLAUDE_MEM_SERVER_BETA_PROJECT_ID="..."
```

On first use in a process you'll see a one-line dim hint on stderr.
What we do with those creds:

- **Write**: every successful `?` / `???` turn becomes a
  `shellllm-turn` observation; `? --remember <fact>` mirrors as a
  `user-fact` observation. Writes are fire-and-forget on a daemon
  thread — they never block your prompt and a network failure is
  silently dropped.
- **Read**: only on the first turn of a brand-new session, we hit
  `/v1/context` with your question and inject the returned text as
  a `<claude-mem-context>` system block.

Controls:

| Knob | What it does |
| --- | --- |
| `SHELLLM_CLAUDE_MEM=0` | Force-disable even when configured |
| `? --no-mem <q>` | Skip integration for one call |
| `? --mem <q>` | Re-enable for one call (overrides env opt-out) |

shellllm's local `--remember` list (`~/.cache/shellllm/memory.jsonl`)
stays the source of truth for offline use; claude-mem just gets a
copy.

## Architecture

```
src/shellllm/
├── safe_fs.py    filesystem hard wall — $HOME/$PWD + inside-HOME denylist
├── client.py     llama-server HTTP client (one-shot + streaming)
├── comma.py      ,    — JSON-schema → fzf picker → stdout
├── ask.py        ?    — streaming agent loop, live markdown render, CLI dispatch
├── search.py     ???  — same loop, web-search-first system prompt
├── state.py      ?:   — long-term facts + cross-session recall subcommands
├── session.py    per-pane conversation persistence (JSONL + idle TTL)
├── memory.py     long-term fact store backing `?: add` / `?: list`
├── compact.py    summary-buffer compaction over the same local model
├── context.py    date/OS/timezone prelude (re-injected on PWD/date change)
├── archive.py    sqlite FTS5 + optional embeddings for `?: recall`
├── embed.py      client for a local llama-server in --embedding mode
├── claude_mem.py optional adapter for claude-mem server-beta (observations + context)
└── web.py        stdlib DuckDuckGo scraper + fetch_url with SSRF guard
tests/test_safe_fs.py        filesystem-wall coverage
tests/test_session.py        TTY id + TTL rotation + JSONL round-trip
tests/test_memory.py         fact store + size cap + archive overflow
tests/test_compact.py        compaction preserves turn boundaries
tests/test_archive.py        FTS5 + cosine recall, RRF fusion, dim-mismatch tolerance
tests/test_embed.py          embedding client + pack/unpack + cosine helpers
tests/test_state.py          ?: subcommands happy + sad paths
tests/test_comma_session.py  , refines across turns; archive on TTL
tests/test_claude_mem.py     adapter gating + payload shape + error swallowing
tests/test_web.py            URL safety + HTML extraction
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
pytest -v   # 168 tests; safe_fs alone covers symlinks, traversal, denylist, lookalikes, truncation
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
| `SHELLLM_CTX` | `32768` | context window (tokens) |
| `SHELLLM_LOG` | `~/.cache/shellllm/llama-server.log` | server log path |
| `SHELLLM_TIMEOUT` | `120` | HTTP timeout (seconds) |
| `SHELLLM_EMBED_URL` | `http://127.0.0.1:8081` | local embedding server endpoint |
| `SHELLLM_EMBED_MODEL` | `local-embed` | model name passed to `/v1/embeddings` |
| `SHELLLM_EMBED_TIMEOUT` | `8` | embedding HTTP timeout (seconds) |
| `SHELLLM_EMBED_PORT` | `8081` | port `?? --start-embed` binds to |
| `SHELLLM_EMBED_CTX` | `2048` | context window for the embedding server |
| `SHELLLM_EMBED_LOG` | `~/.cache/shellllm/llama-embed.log` | embedding-server log path |
| `SHELLLM_ARCHIVE_DB` | `~/.cache/shellllm/archive.db` | sqlite archive of expired sessions |
| `SHELLLM_AUTO_RECALL` | unset | set to `1` to auto-inject archive hits on first-turn questions |
| `SHELLLM_CLAUDE_MEM` | unset (auto) | set to `0` to disable claude-mem integration even when configured |
| `CLAUDE_MEM_SERVER_BETA_URL` | — | claude-mem server-beta base URL (enables integration) |
| `CLAUDE_MEM_SERVER_BETA_API_KEY` | — | bearer token for claude-mem server-beta |
| `CLAUDE_MEM_SERVER_BETA_PROJECT_ID` | — | project id observations are scoped to |

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -v
```

## License

[MIT](LICENSE)
