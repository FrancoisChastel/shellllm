# Configuration

Everything beyond the 3-step install. Pick what you need.

- [Model tiers](#model-tiers)
- [Sessions](#sessions)
- [Terminal context](#terminal-context)
- [Semantic recall (optional)](#semantic-recall-optional)
- [Cross-session memory (optional)](#cross-session-memory-optional)
- [The hard wall](#the-hard-wall)
- [Environment variables](#environment-variables)
- [Install from source](#install-from-source)
- [Use a hosted API instead of llama-server](#use-a-hosted-api-instead-of-llama-server)
- [JS rendering for `fetch_url`](#js-rendering-for-fetch_url)
- [What's deliberately not built](#whats-deliberately-not-built)

## Model tiers

Three preset tiers, named for what you'd reach for:

| Tier | Model | Notes |
|---|---|---|
| `fast` | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` | MoE, 3B active params + self-speculative MTP. Fastest on Apple Silicon. |
| `balanced` | `unsloth/Qwen3.6-27B-GGUF` (Q4_K_M) | Dense 27B. Default. |
| `smart` | `unsloth/Qwen3-Coder-Next-GGUF` | Latest coder-tuned model. Best for shell/agent work. |

```sh
huggingface-cli download unsloth/Qwen3-Coder-Next-GGUF
?? --start smart
```

`??` finds the GGUF inside your HuggingFace cache — no path config required.

Each tier binds to its own port (`fast` :8091, `balanced` :8080, `smart` :8093), so tiers can serve **side by side** — and a single call can be routed to whichever fits:

```sh
?? --start fast                 # fast tier up, alongside balanced
, --fast rename all .jpeg to .jpg    # this one call uses the fast tier
? --smart why is my Makefile rebuilding everything   # this one, the smart tier
?? --status                     # which tiers are up
?? --stop fast                  # stop just that one
```

## Sessions

Each pane × command gets a sticky JSONL session at `~/.cache/shellllm/sessions/`. Pane identity is `TERM_SESSION_ID` (Terminal.app / iTerm) → `TMUX_PANE` → `WINDOWID` → `$PPID`, first one that resolves.

```sh
? what was that flag for ripgrep
? and with json output            # ← model still knows "ripgrep"
? --history                       # transcript of this pane
? --new <new question>            # start a fresh session
? --reset                         # drop the current one
? --compact                       # force compaction now
```

When the conversation crosses 80% of `SHELLLM_CTX`, older turns are auto-summarized into a single `<summary-so-far>` block by the same local model; the most recent 4 stay verbatim.

Every expired or `--new`'d session flows into `~/.cache/shellllm/archive.db` (sqlite + FTS5) so `???` can search across panes and days.

## Terminal context

The terminal knows what just happened — shellllm uses it, at a level you control. One env var, a ladder of levels:

```sh
export SHELLLM_SHELL_CONTEXT=off        # capture nothing
export SHELLLM_SHELL_CONTEXT=cmd        # previous command + exit status (default)
export SHELLLM_SHELL_CONTEXT=history    # + last 10 commands
export SHELLLM_SHELL_CONTEXT=output     # + recent pane output (tmux only)
```

Who uses it: `?` always (so "why did that fail" just works), `,,` on demand (doubling the glyph = bring the context), and plain `,` never.

```sh
$ git push origin amin
error: src refspec amin does not match any
$ ,,                              # bare ,, = fix → proposes: git push origin main
$ ? why did that fail             # the model sees the command and its exit status

$ tar -czf logs.tgz var/log/app
$ ,, verify it and show the largest entries   # ",, <prompt>" = propose with context
```

How it stays private:

- **Local-first**: with a local model, nothing leaves the machine anyway. The ladder matters when you point `SHELLLM_BASE_URL` at a hosted API.
- **Redaction**: captures pass through a secret scrubber (`KEY=…` assignments, `Bearer` headers, AWS/GitHub/Slack/Stripe-shaped tokens, JWTs) before the model sees them. Git SHAs survive — they're useful.
- **Per-call env, never exported**: the zsh layer passes captures as one-shot environment for the single invocation; nothing lingers in your shell.
- **Both sides enforce the ladder**: zsh won't capture above your level, and the Python side independently re-checks it.
- **Ephemeral**: context blocks are rebuilt per turn and never persisted into sessions or the archive.
- `--no-ctx` skips injection for one call; piped stdin (`cmd | ? …`) is its own explicit consent and works regardless of the ladder.

## Semantic recall (optional)

Recall works in BM25-only mode out of the box. Adding a tiny embedding server upgrades it to **hybrid semantic + BM25** (fused with Reciprocal Rank Fusion):

```sh
huggingface-cli download Qwen/Qwen3-Embedding-0.6B-GGUF
?? --start-embed                # second llama-server in --embedding mode on :8081
export SHELLLM_AUTO_RECALL=1    # auto-inject prior context on first-turn questions
```

Three embedding tiers:

| Tier | Model | Notes |
|---|---|---|
| `tiny` | `Qwen/Qwen3-Embedding-0.6B-GGUF` | Same family as the chat tiers (default). |
| `bge` | `ChristianAzinn/bge-small-en-v1.5-gguf` | Tiny English-only, very fast. |
| `nomic` | `nomic-ai/nomic-embed-text-v1.5-GGUF` | Strong general-purpose. |

Mismatched embedding dims (when you swap models) are silently skipped — old rows still serve BM25 hits.

## Cross-session memory (optional)

If you also use [claude-mem](https://github.com/thedotmack/claude-mem) in server-beta mode, shellllm writes each turn as an observation and pulls relevant prior context on a fresh session:

```sh
export CLAUDE_MEM_SERVER_BETA_URL="https://your-host"
export CLAUDE_MEM_SERVER_BETA_API_KEY="..."
export CLAUDE_MEM_SERVER_BETA_PROJECT_ID="..."
```

Without those vars the integration is inert. With them: writes are fire-and-forget on a daemon thread; reads happen only on the first turn of a new session; failures never propagate.

## The hard wall

Every file read through `?` goes through `safe_fs.safe_read`. Four rules, all enforced:

1. **Canonicalize** with `.resolve(strict=True)` — symlinks and `..` flattened before containment is checked.
2. **Contain** to `$HOME` or `$PWD`. Anywhere else refuses with `WallViolation`.
3. **Deny inside-HOME secrets.** Paths under `.ssh`, `.aws`, `.gnupg`, `.kube`, `Library/Keychains`, `.netrc`, etc. refuse. Match is by path component — `.sshfoo` is allowed.
4. **Regular files only.** Devices, fifos, sockets, directories refuse.

Reads cap at 1 MB and use `O_NOFOLLOW` as a belt against a resolve-then-open symlink race.

```sh
pytest -v   # 257 tests; 38 dedicated to symlinks, traversal, denylist, lookalikes, truncation
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SHELLLM_BASE_URL` | `http://127.0.0.1:8080` | llama-server (or hosted) endpoint |
| `SHELLLM_API_KEY` | — | Bearer auth for chat — set when pointing at a hosted API |
| `SHELLLM_MODEL` | `local` | Model id passed in chat requests (override per provider) |
| `SHELLLM_PORT` | `8080` | Server port (default route + `balanced` tier) |
| `SHELLLM_PORT_FAST` | `8091` | `fast` tier port |
| `SHELLLM_PORT_BALANCED` | `$SHELLLM_PORT` | `balanced` tier port |
| `SHELLLM_PORT_SMART` | `8093` | `smart` tier port |
| `SHELLLM_SHELL_CONTEXT` | `cmd` (zsh layer) | `off` / `cmd` / `history` / `output` — terminal-context ladder |
| `SHELLLM_AUTOSTART` | unset | `1` to auto-start the default tier when `,` / `?` find it down |
| `SHELLLM_NGL` | `99` | GPU offload layers |
| `SHELLLM_CTX` | `32768` | Context window (tokens) |
| `SHELLLM_TIMEOUT` | `120` | HTTP timeout (seconds) |
| `SHELLLM_LLAMA_MODEL` | — | Explicit GGUF path, overrides tier |
| `SHELLLM_LOG` | `~/.cache/shellllm/llama-server.log` | Server log path |
| `SHELLLM_RENDER_URL` | — | Firecrawl-compatible JS-render endpoint (enables) |
| `SHELLLM_RENDER_API_KEY` | — | Bearer auth for the render service |
| `SHELLLM_RENDER_TIMEOUT` | `30` | Render-service HTTP timeout (seconds) |
| `SHELLLM_EMBED_URL` | `http://127.0.0.1:8081` | Local (or hosted) embedding endpoint |
| `SHELLLM_EMBED_API_KEY` | — | Bearer auth for embeddings (falls back to `SHELLLM_API_KEY`) |
| `SHELLLM_EMBED_PORT` | `8081` | Embedding server port |
| `SHELLLM_EMBED_CTX` | `2048` | Embedding context window |
| `SHELLLM_EMBED_MODEL` | `local-embed` | Model name passed to `/v1/embeddings` |
| `SHELLLM_EMBED_TIMEOUT` | `8` | Embedding HTTP timeout (seconds) |
| `SHELLLM_EMBED_LOG` | `~/.cache/shellllm/llama-embed.log` | Embedding-server log path |
| `SHELLLM_ARCHIVE_DB` | `~/.cache/shellllm/archive.db` | sqlite archive of expired sessions |
| `SHELLLM_AUTO_RECALL` | unset | `1` to auto-inject archive hits on first-turn questions |
| `SHELLLM_CLAUDE_MEM` | unset (auto) | `0` to disable claude-mem even when configured |
| `CLAUDE_MEM_SERVER_BETA_URL` | — | claude-mem server-beta base URL (enables integration) |
| `CLAUDE_MEM_SERVER_BETA_API_KEY` | — | Bearer token |
| `CLAUDE_MEM_SERVER_BETA_PROJECT_ID` | — | Project id observations are scoped to |

## Install from source

```sh
python3 -m venv .venv
.venv/bin/pip install -e .

echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> ~/.zshrc
echo "source $PWD/zsh/shellllm.zsh"          >> ~/.zshrc
exec zsh
```

After a `git pull` you only need `exec zsh` to pick up updates to `zsh/shellllm.zsh`. Python entry-points reload automatically (editable install).

## Use a hosted API instead of llama-server

shellllm's chat and embedding paths are both OpenAI-compatible. Point them at any provider, BYOK:

```sh
# OpenAI
export SHELLLM_BASE_URL="https://api.openai.com"
export SHELLLM_API_KEY="sk-..."
export SHELLLM_MODEL="gpt-4o-mini"

# OpenRouter
export SHELLLM_BASE_URL="https://openrouter.ai/api"
export SHELLLM_API_KEY="sk-or-..."
export SHELLLM_MODEL="anthropic/claude-3.5-sonnet"

# Groq (very fast)
export SHELLLM_BASE_URL="https://api.groq.com/openai"
export SHELLLM_API_KEY="gsk-..."
export SHELLLM_MODEL="llama-3.3-70b-versatile"
```

When `SHELLLM_API_KEY` is set, every chat request carries `Authorization: Bearer …`. Without it, requests stay anonymous (which is what the local `llama-server` wants). The same fall-through applies to embeddings: set `SHELLLM_EMBED_API_KEY` for a separate provider, or let it inherit `SHELLLM_API_KEY` when both endpoints share auth.

Mix and match: local chat + hosted embeddings, hosted chat + local embeddings, or both hosted. The model decides; shellllm just plumbs.

## JS rendering for `fetch_url`

Static HTML works for most pages. SPAs (React/Vue/Svelte sites that paint after the initial response) come back empty. To handle them, point `fetch_url` at a Firecrawl-compatible scraper — hosted or self-hosted:

```sh
# Hosted: https://firecrawl.dev
export SHELLLM_RENDER_URL="https://api.firecrawl.dev"
export SHELLLM_RENDER_API_KEY="fc-..."

# Self-hosted: https://github.com/mendableai/firecrawl
export SHELLLM_RENDER_URL="http://localhost:3002"
export SHELLLM_RENDER_API_KEY="any-string-if-disabled"
```

When configured, every `fetch_url` call tries `POST {url}/v1/scrape` first (Bearer auth, asks for markdown). On any failure — connection refused, 4xx/5xx, timeout — it falls back transparently to the static fetcher. Without the env vars, behavior is unchanged.

Any service speaking the same `/v1/scrape` shape works. Other vendors can be bridged with a tiny proxy that translates between their API and Firecrawl's.

## What's deliberately not built

- **GBNF prefix grammar for `,`.** JSON schema is enough for v1; the system prompt forbids the obvious destructive commands.
- **Local browser-based JS rendering.** Skipped on purpose — a 300MB Chromium dependency doesn't match the offline-by-default story. The BYOK `SHELLLM_RENDER_URL` path above is the supported alternative.
