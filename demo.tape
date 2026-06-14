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
# piping beat), and seeds the archive with one prior conversation about
# git stash (so the `???` recall finds a real, pre-existing hit).

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
Type "export SHELLLM_ARCHIVE_DB=/tmp/shellllm-demo/archive.db SHELLLM_MEMORY_FILE=/tmp/shellllm-demo/memory.jsonl SHELLLM_SESSIONS_DIR=/tmp/shellllm-demo/sessions SHELLLM_SHELL_CONTEXT=cmd"
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
# Seed one prior shellllm session into the archive so `??? git` finds
# something later in the demo. Heredoc keeps the quoting manageable.
Type@10ms "python3 - <<'PY'"
Enter
Type@10ms "from shellllm.archive import Archive"
Enter
Type@10ms "Archive().ingest_session(cmd='ask', terminal_id='demo', created_at=0, last_used=0, last_pwd='~/code/api', last_date='2026-06-08', turn_count=1, messages=[{'role':'user','content':'how does git stash work'},{'role':'assistant','content':'git stash temporarily shelves uncommitted changes so you can switch contexts, then `git stash pop` re-applies them on top of the current working tree.'}])"
Enter
Type@10ms "PY"
Enter
Sleep 400ms
# Warm BOTH tiers so the first visible beats don't pay cold-start
# latency. SHELLLM_BASE_URL targets a specific tier; Python's stdlib
# urllib avoids VHS's parser choking on JSON braces.
Type@10ms "python3 - <<'PY'"
Enter
Type@10ms "import os, json, urllib.request"
Enter
Type@10ms "for port in (8091, 8080):"
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

# ── Beat 1 — `, --fast` proposes a command via the fast tier ────────────
# You're in a directory full of build artifacts. You vaguely remember
# `du` exists. Ask the model — routed to the fast tier (MoE 3B-active,
# ~3× faster than balanced).
Type ", --fast find the five largest files in this directory"
Sleep 600ms
Enter
Wait+Screen@30s /enter: drop on prompt/
Sleep 600ms
Enter
Sleep 1s
Enter
Sleep 3s

# ── Beat 2 — `,,` fixes the previous command (still on fast) ────────────
# A real flag typo, the kind people make daily. `--grpe` instead of
# `--grep`. The shell prints "unknown option" with exit status 129.
# Bare `,,` brings the terminal context (command + status) and lets the
# fast tier propose the fix.
Type "git log --oneline -n 10 --grpe 'fix'"
Sleep 500ms
Enter
Sleep 1500ms
Type ",, --fast"
Sleep 800ms
Enter
Wait+Screen@30s /enter: drop on prompt/
Sleep 600ms
Enter
Sleep 1s
Enter
Sleep 4s
Type "clear"
Enter
Sleep 400ms

# ── Beat 3 — `? --fast` for a quick question ────────────────────────────
# `git stash` came to mind and you blanked. The fast tier handles this
# in seconds.
Type "? --fast in markdown and 3 lines max, what does git stash do"
Sleep 600ms
Enter
Sleep 14s

# ── Beat 4 — switch to the balanced tier for the harder question ────────
# The build fails. The piped traceback IS the context. For a real
# diagnosis we want the dense 27B — `--balanced` routes this one call
# to the bigger model. Same flow, different muscle.
Type "python3 build.py 2>&1 | ? --balanced one line: what is broken here"
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
# "What was that git thing I asked the other day?" Bare query → search
# across every archived conversation in this pane and others.
Type "??? git stash"
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
