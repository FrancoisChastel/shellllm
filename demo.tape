# shellllm — visual demo for the README hero.
#
# Renders a ~35s GIF showing the four-glyph CLI in action: `,` proposes
# commands with sticky-session refinement, `?` answers, `???` recalls
# across prior sessions, `??? --archives` browses what's been saved.
#
# Render
#   brew install vhs                # one-time
#   ?? --start balanced             # start any tier; llama-server must be up
#   vhs demo.tape                   # writes demo.gif
#
# Then reference from README:
#   ![demo](demo.gif)
#
# Re-run after CLI changes — VHS is deterministic, so commits stay
# reproducible. Tweak typing/playback speed via the Set lines below.

Output demo.gif

# ── Look ────────────────────────────────────────────────────────────────
Set Theme "Catppuccin Mocha"
Set FontSize 16
Set FontFamily "JetBrains Mono"
Set Width 1100
Set Height 640
Set Padding 24
Set TypingSpeed 55ms
Set PlaybackSpeed 1.0
Set Shell "zsh"

# Hide the "shell is starting" noise; reveal on a clean prompt.
Hide
Type "clear"
Enter
Sleep 200ms
Show

# ── 1. `,` proposes a command, fzf picks, prompt drops it ───────────────
Type ", find the five largest files in this directory"
Sleep 600ms
Enter
Sleep 4s              # model thinks + fzf renders
Enter                 # accept the highlighted proposal
Sleep 800ms
Enter                 # execute the dropped command for real
Sleep 3s

# ── 2. Refine via the same `,` session ──────────────────────────────────
# The model remembers the prior proposal — `, the same but…` works because
# `,` keeps a sticky per-pane thread (see Sessions in README).
Type ", the same but only ones modified today"
Sleep 400ms
Enter
Sleep 4s
Enter                 # accept the refined proposal
Sleep 800ms
Ctrl+U                # clear without executing — just showing the refinement
Sleep 1s

# ── 3. `?` answers ──────────────────────────────────────────────────────
Type "? in markdown, what does git stash do"
Sleep 400ms
Enter
Sleep 8s              # streaming markdown render

# ── 4. `???` recalls across past sessions ───────────────────────────────
# Bare query → BM25 search over the archive of every prior `,` and `?`.
# Returns the answer we just got, plus anything else mentioning git stash.
Type "??? git stash"
Sleep 400ms
Enter
Sleep 3s

# ── 5. `??? --archives` browses what's saved without a query ────────────
Type "??? --archives 5"
Sleep 400ms
Enter
Sleep 4s

# ── Outro — let the final frame breathe ─────────────────────────────────
Sleep 2s
