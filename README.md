# shellllm

[![ci](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml/badge.svg)](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

> A local LLM at your shell prompt. Five glyphs, no account, works offline.

`shellllm` puts a local language model behind a handful of punctuation commands in your shell. Describe what you want in English and get a real shell command back. Mistype something and recover with two keystrokes. Ask a question without leaving the terminal. Search every conversation you've ever had with the tool.

Everything runs against a local [`llama.cpp`](https://github.com/ggerganov/llama.cpp) server — no API keys, no data leaves your machine, no cost per question, available on a plane.

![shellllm demo](demo.gif)

## Install

Three steps. macOS or Linux, local model, no account required.

```sh
# 1. Tool (pulls llama.cpp, fzf, and the CLIs)
brew install FrancoisChastel/shellllm/shellllm
echo 'source "$(brew --prefix)/share/shellllm/shellllm.zsh"' >> ~/.zshrc
exec zsh

# 2. Model (one-time download, ~16 GB)
"$(brew --prefix)/share/shellllm/download-models.sh"
#  ↑ fetches the default tier. Pass `fast`, `smart`, or `all` for more.
#  ↑ needs huggingface-cli — `pipx install huggingface_hub` if missing.

# 3. Run
??                          # start the local server (~10s once cached)
, find the five largest files here
```

Don't want to manage the server yourself? `export SHELLLM_AUTOSTART=1` and the first `,` or `?` starts it on demand.

Not on zsh? The Python CLIs (`shellllm-comma`, `shellllm-ask`, `shellllm-recall`) work in bash, fish, or any POSIX shell. See [CONFIGURATION.md#using-shellllm-without-zsh](CONFIGURATION.md#using-shellllm-without-zsh) for a minimal bash adapter.

## The five commands

| Command | What it does | Example |
|---|---|---|
| `, <prompt>` | Propose 3–5 shell commands, pick one in fzf, drop on prompt. Never executes. | `, the five largest files here` |
| `,, [prompt]` | Same, but with terminal context attached. Bare `,,` repairs the previous command. | `,,` after a typo'd command |
| `? <question>` | Ask the model. Streams markdown, keeps a per-pane conversation. Reads piped input. | `? what does git stash do` |
| `???` | Memory and recall. Bare query searches the archive; flags pin long-term facts. | `??? --add I prefer ripgrep` |
| `??` | Start, stop, or check the local `llama-server`. | `?? --start fast` |

A few moves worth knowing:

```sh
make 2>&1 | ? what broke         # pipe an error, get a diagnosis
?? --start fast                  # two tiers can serve side by side
, --smart explain this Makefile  # route one call to the bigger model
??? --add the project uses pnpm  # pin a fact; every `?` carries it
??? docker volumes               # bare query → search past sessions
```

That's the whole surface. **For model tiers, hosted-API setup, the terminal-context ladder, semantic recall, JS rendering, the full environment-variable table, and the filesystem hard wall, see [CONFIGURATION.md](CONFIGURATION.md).**

## Design

A few decisions are load-bearing:

- **`,` never executes.** The comma proposes commands and drops the chosen one on your prompt line. You confirm with Enter. The model never runs anything on its own.
- **The filesystem has a hard wall.** `?` can read files, but only inside `$HOME` or `$PWD`, never inside `.ssh`, `.aws`, `.gnupg`, or any other secret-bearing path. Symlinks are canonicalised before containment is checked. Reads cap at 1 MB.
- **Terminal context is a ladder.** What `shellllm` sees from your terminal — the previous command, its exit status, recent history, recent output — is gated by `SHELLLM_SHELL_CONTEXT`, redacted for secrets, rebuilt per call, and never persisted.
- **Sessions are sticky per pane, ephemeral per session.** Each terminal pane has its own conversation thread; idle sessions roll into a searchable archive automatically.

## Why local

You don't need a frontier model to remember `tar -czvf`. The questions you ask between `git commit` and `make test` — flag lookups, "what does this command do", "fix my typo" — are well within reach of a local 27B model. In exchange you get privacy, sub-second latency, no per-question cost, and a prompt that works on a flight.

For the harder questions, `shellllm` doesn't force a choice: point `SHELLLM_BASE_URL` at OpenAI, OpenRouter, Groq, or any OpenAI-compatible endpoint, and the same five glyphs route through a hosted model instead. Mix and match — local chat with hosted embeddings, hosted chat with local recall, whatever fits.

## For contributors and AI agents

Conventions, the load-bearing invariants in detail, test rules, and where to look first: see [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
