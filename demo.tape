# shellllm — flow demo
#
# A ~75-second story: you ask for a command instead of googling (`,`),
# you mistype a flag and fix it with two keystrokes (`,,`), you ask a
# question without breaking flow (`?`), you pipe a real error into a
# bigger model for the diagnosis, you pin a fact, and you recall
# something you asked days ago (`???`) — all at the prompt.
#
# Render
#   brew install vhs                           # one-time, ~30s
#   ?? --start fast && ?? --start balanced     # both tiers up
#   vhs demo.tape                              # writes demo.gif
#
# The Hide block sets up a sandbox in /tmp/shellllm-demo with sized
# files (so `du` has output), a deliberately broken build.py (for the
# piping beat), and seeds the archive with one prior conversation
# about Docker volumes (so the `???` recall finds a real, pre-existing
# hit later — the kind of "what did past me ask?" that recall is for).

Output demo.gif

# ── Look ────────────────────────────────────────────────────────────────
Require zsh
Require llama-server
Set Shell "zsh"
Set FontSize 18
# SF Mono matches macOS Terminal.app/iTerm defaults. Fallback chain stays
# valid on Linux dev boxes too.
Set FontFamily "SF Mono"
Set Theme "Catppuccin Mocha"
Set Width 1200
Set Height 720
Set Padding 28
Set BorderRadius 10
Set WindowBar Colorful
Set TypingSpeed 60ms
Set PlaybackSpeed 1.0
Set CursorBlink true

# ── Setup (hidden) ──────────────────────────────────────────────────────
# Fresh sandbox, isolated archive/memory paths so we don't touch the
# user's real cache while recording.
Hide
# Self-contained: the VHS zsh is bare, so wire up the CLIs and glyphs
# from the repo checkout we're rendering in.
Type "export PATH=$PWD/.venv/bin:$PATH && source ./zsh/shellllm.zsh"
Enter
Sleep 200ms
Type "export SHELLLM_ARCHIVE_DB=/tmp/shellllm-demo/archive.db SHELLLM_MEMORY_FILE=/tmp/shellllm-demo/memory.jsonl SHELLLM_SESSIONS_DIR=/tmp/shellllm-demo/sessions SHELLLM_SHELL_CONTEXT=cmd SHELLLM_BASE_URL=http://127.0.0.1:8091"
Enter
Sleep 100ms
Type "rm -rf /tmp/shellllm-demo && mkdir -p /tmp/shellllm-demo/sessions && cd /tmp/shellllm-demo"
Enter
Sleep 100ms
# Some real-sized files so `du -ah | sort -hr | head -5` has output.
Type "head -c 1500000 </dev/urandom > release-build.tar.gz"
Enter
Type "head -c 920000 </dev/urandom > node_modules.cache"
Enter
Type "head -c 410000 </dev/urandom > coverage-report.html"
Enter
Type "head -c 180000 </dev/urandom > webpack-stats.json"
Enter
# A build script that fails with a real traceback, for the piping beat.
Type "echo 'import tomlib' > build.py"
Enter
# Seed one prior shellllm session into the archive so `??? docker`
# finds something later in the demo — a realistic "what did I ask the
# other day?" hit. Heredoc keeps the quoting manageable.
Type@10ms "python3 - <<'PY'"
Enter
Type@10ms "from shellllm.archive import Archive"
Enter
Type@10ms "Archive().ingest_session(cmd='ask', terminal_id='demo', created_at=0, last_used=0, last_pwd='~/code/api', last_date='2026-06-08', turn_count=1, messages=[{'role':'user','content':'how do docker volumes work'},{'role':'assistant','content':'A Docker volume is a managed filesystem owned by the Docker daemon that you mount into a container; it survives container restarts and is the recommended way to persist data (vs. bind mounts, which point at a host path).'}])"
Enter
Type@10ms "PY"
Enter
Sleep 400ms
# Warm all three tiers so the visible beats don't pay cold-start
# latency. SHELLLM_BASE_URL targets a specific tier; Python's stdlib
# urllib avoids VHS's parser choking on JSON braces.
Type@10ms "python3 - <<'PY'"
Enter
Type@10ms "import os, json, urllib.request"
Enter
Type@10ms "for port in (8091, 8080, 8093):"
Enter
Type@10ms "    req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=json.dumps({'model':'local','messages':[{'role':'user','content':'hi'}],'max_tokens':4}).encode(), headers={'Content-Type':'application/json'})"
Enter
Type@10ms "    try: urllib.request.urlopen(req, timeout=20).read()"
Enter
Type@10ms "    except Exception: pass"
Enter
Type@10ms "PY"
Enter
Sleep 18s
Type "clear"
Enter
Sleep 200ms
Show

# ── Beat 0 — spin up the local model ────────────────────────────────────
# The audience needs to know the LLM lives on your machine. `?? --start
# fast` puts a llama-server on :8091 with the MoE 3B-active tier — the
# default route in this demo. Two other tiers (`balanced`, `smart`)
# also up and addressable per-call.
Type "?? --status"
Sleep 600ms
Enter
Sleep 3s

# ── Beat 1 — `,` proposes a command ─────────────────────────────────────
# You're in a directory of build artifacts. You vaguely remember `du`
# exists. Ask the model in-band — no flag needed; SHELLLM_BASE_URL is
# already pointing at the fast tier.
Type ", find the five largest files in this directory"
Sleep 600ms
Enter
Wait+Screen@30s /enter: drop on prompt/
Sleep 600ms
Enter
Sleep 1s
Enter
Sleep 3s

# ── Beat 2 — `,,` diagnoses then fixes the previous command ─────────────
# A real flag typo on `find`. BSD and GNU find both accept `-type`, so
# the corrected command works on every platform — `,,` diagnoses the
# typo in one stderr line and proposes the corrected command first.
Type "find . -tpye f -name '*.py'"
Sleep 500ms
Enter
Sleep 1500ms
Type ",,"
Sleep 800ms
Enter
Wait+Screen@30s /enter: drop on prompt/
Sleep 800ms
Enter
Sleep 1s
Enter
Sleep 4s
Type "clear"
Enter
Sleep 400ms

# ── Beat 3 — quick contextual question, default tier (fast) ─────────────
# A flag you just saw in Beat 1 (`du -k`). The fast tier answers in
# seconds, no flag needed because SHELLLM_BASE_URL is already pointing
# at fast.
Type "? in 2 lines, what does the -k flag mean in du"
Sleep 600ms
Enter
Sleep 14s

# ── Beat 4 — switch to the smart tier for the harder question ───────────
# The build fails. The piped traceback IS the context. The smart tier
# (Qwen3-Coder-Next) is tuned for code — `--smart` routes this one
# call to it. Same flow, different model, picked per-call.
Type "python3 build.py 2>&1 | ? --smart one line: what is broken here"
Sleep 600ms
Enter
Sleep 16s
Type "clear"
Enter
Sleep 400ms

# ── Beat 5 — pin a long-term fact ───────────────────────────────────────
# While you're here, pin something. Now every `?` carries it.
Type "??? --add this project uses pnpm and ships via release-build.tar.gz"
Sleep 500ms
Enter
Sleep 2s

# ── Beat 6 — `???` recalls across past sessions ─────────────────────────
# "What did past me ask about Docker?" Bare query → search across
# every archived conversation in this pane and others.
Type "??? docker volume"
Sleep 500ms
Enter
Sleep 5s

# ── Beat 7 — `??? --archives` shows what's in the vault ─────────────────
Type "??? --archives 5"
Sleep 500ms
Enter
Sleep 5s

# ── Outro ───────────────────────────────────────────────────────────────
Sleep 3s
