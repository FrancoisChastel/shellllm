# shellllm — flow demo
#
# Tells a 60-second story: you're working in a directory, you hit a
# command you don't quite remember, you ask the model about it, you
# refine, you move on, and later you recall a thing you asked earlier
# — all without leaving your zsh prompt.
#
# Render
#   brew install vhs                           # one-time, ~30s
#   ?? --start balanced                        # llama-server must be running
#   vhs demo.tape                              # writes demo.gif
#
# The Hide block at the top sets up a sandbox in /tmp/shellllm-demo with
# a few sized files (so `du` has something to show) and seeds the
# archive with one prior conversation about git stash (so the `???`
# recall later finds a real, pre-existing hit). Everything else is the
# tool acting in its native environment.

Output demo.gif

# ── Look ────────────────────────────────────────────────────────────────
Require zsh
Require llama-server
Set Shell "zsh"
Set FontSize 18
Set FontFamily "JetBrainsMono Nerd Font"
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
Type "export SHELLLM_ARCHIVE_DB=/tmp/shellllm-demo/archive.db SHELLLM_MEMORY_FILE=/tmp/shellllm-demo/memory.jsonl SHELLLM_SESSIONS_DIR=/tmp/shellllm-demo/sessions"
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
Type "touch -d '2 hours ago' release-build.tar.gz coverage-report.html"
Enter
# Seed one prior shellllm session into the archive so `??? git` finds
# something later in the demo. We use a heredoc to keep the quoting
# manageable inside the Type command.
Type@10ms "python3 - <<'PY'"
Enter
Type@10ms "from shellllm.archive import Archive"
Enter
Type@10ms "Archive().ingest_session(cmd='ask', terminal_id='demo', created_at=0, last_used=0, last_pwd='~/code/api', last_date='2026-06-08', turn_count=1, messages=[{'role':'user','content':'how does git stash work'},{'role':'assistant','content':'git stash temporarily shelves uncommitted changes so you can switch contexts, then `git stash pop` re-applies them on top of the current working tree.'}])"
Enter
Type@10ms "PY"
Enter
Sleep 400ms
Type "clear"
Enter
Sleep 200ms
Show

# ── Beat 1 — `,` proposes a command ─────────────────────────────────────
# You're in a directory full of build artifacts. You vaguely remember
# `du` exists. Instead of googling, you ask the model in-band.
Type ", find the five largest files in this directory"
Sleep 600ms
Enter
# Wait for the model + fzf to render the candidate picker.
Sleep 6s
# Accept the highlighted proposal — `print -z` drops it on the prompt.
Enter
Sleep 800ms
# Run it.
Enter
Sleep 3s

# ── Beat 2 — refine via the sticky session ──────────────────────────────
# Same `,` thread in this pane: the model remembers the prior proposal.
# "the same but only ones modified today" works because it has
# context, exactly like a real conversation.
Type ", the same but only ones modified in the last hour"
Sleep 600ms
Enter
Sleep 6s
Enter
Sleep 800ms
Enter
Sleep 3s

# ── Beat 3 — `?` answers without breaking the flow ──────────────────────
# Two files made the cut. Now you need to commit, but `git stash` came
# to mind and you blanked. Ask the model in-band, get an answer.
Type "? in markdown and 3 lines max, what does git stash do"
Sleep 600ms
Enter
# Streaming markdown render — generous sleep because turn length varies
# with the local model.
Sleep 14s

# ── Beat 4 — pin a long-term fact ───────────────────────────────────────
# While you're here, pin something. Now every `?` carries it.
Type "??? --add this project uses pnpm and ships via release-build.tar.gz"
Sleep 500ms
Enter
Sleep 2s

# ── Beat 5 — start a fresh `?` thread so the prior turn lands in the archive
# `--new` archives the current session — perfect mid-flow segue.
Type "? --new"
Sleep 500ms
Enter
Sleep 2s
Type "clear"
Enter
Sleep 400ms

# ── Beat 6 — `???` recalls across past sessions ─────────────────────────
# "What was that git thing I asked earlier?" Bare query → FTS5 search
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
# Let the final frame breathe so a viewer can read the last row.
Sleep 3s
