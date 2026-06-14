# shellllm

[![ci](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml/badge.svg)](https://github.com/FrancoisChastel/shellllm/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

> Local LLM at your zsh prompt. Five glyphs, no API key, works offline.

Drop English at your prompt and get a real shell command. Typo something and fix it with two keystrokes. Ask the model a question without breaking flow. Search every past conversation by content. The whole CLI is punctuation — `,` `,,` `?` `??` `???` — because the best terminal UI is the one that fits next to `cd` and `ls`.

And it knows what just happened in your terminal: the previous command and its exit status ride along (redacted, local, [level-controlled](CONFIGURATION.md#terminal-context)), so "that" and "why did it fail" mean what you think they mean.

![shellllm demo](demo.gif)

No API key. No data leaves your machine. Works with WiFi off (except `? --web`).

## Install

Three steps. Local model, no account.

```sh
# 1. Tool (pulls llama.cpp, fzf, and the CLIs)
brew install FrancoisChastel/shellllm/shellllm
echo 'source "$(brew --prefix)/share/shellllm/shellllm.zsh"' >> ~/.zshrc
exec zsh

# 2. Model (one-time; `pipx install huggingface_hub` if you lack the CLI)
huggingface-cli download unsloth/Qwen3.6-27B-GGUF

# 3. Go
??                                  # start the server (~10s once cached)
, find the five largest files here
```

Zero babysitting: `export SHELLLM_AUTOSTART=1` and the first `,` or `?` starts the server for you.

## The five commands

| Cmd | What | Example |
|---|---|---|
| `, <english>` | Propose shell commands, pick one in fzf, drop on prompt. Never executes. | `, the five largest files here` |
| `,, [english]` | Same, but **with terminal context**. Bare `,,` = fix the previous command. | `,,` after a typo'd push |
| `? <q>` | Ask the model. Streams markdown. Sticky per-pane session. Pipe-friendly. | `? what does git stash do` |
| `???` | Memory & recall. Bare query searches the archive. Flags pin long-term facts. | `??? --add I prefer ripgrep` |
| `??` | Start / stop / status the local `llama-server`. | `?? --start fast` |

A few moves worth knowing:

```sh
make 2>&1 | ? what broke         # pipe an error in, get a diagnosis
?? --start fast                  # multiple tiers run side by side
, --smart explain this Makefile  # route one call to a specific tier
??? --add the project uses pnpm  # pin a fact; every `?` carries it
??? docker volumes               # bare query = recall across past sessions
```

That's the whole tour. **For everything else — model tiers, hosted-API setup, terminal-context ladder details, semantic recall, JS rendering for SPAs, the full env-var table, and the filesystem hard wall — see [CONFIGURATION.md](CONFIGURATION.md).**

## Why this exists

You don't need to ship every "what does git stash do" question to a frontier model. The wifi will be off on the plane and you'll still want a hand. Every `tar -czvf` answer has been in your model's training data for two years. Claude Code is great but it lives in its own window — `cd ~/project && ?` shouldn't require a browser tab.

The bet: a local 27B model is roughly equivalent to a frontier model for the questions you ask between `git commit` and `make test`. The wins — privacy, latency, offline availability, $0 per question — are real, every day.

## For contributors and AI agents

Coding conventions, the load-bearing invariants (the **hard wall**, the **comma never executes**, the **terminal-context ladder**), test rules, and where to look: see [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
