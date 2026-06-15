# shellllm — source this from .zshrc
#
#   source /path/to/shellllm/zsh/shellllm.zsh
#
# Assumes `shellllm-comma` and `shellllm-ask` are on $PATH (pip-installed,
# or the project venv's bin/ is on PATH).

: ${SHELLLM_COMMA:=shellllm-comma}
: ${SHELLLM_ASK:=shellllm-ask}
: ${SHELLLM_RECALL:=shellllm-recall}
: ${SHELLLM_PORT:=8080}
: ${SHELLLM_EMBED_PORT:=8081}
: ${SHELLLM_EMBED_CTX:=2048}
: ${SHELLLM_NGL:=99}
: ${SHELLLM_CTX:=32768}
: ${SHELLLM_LOG:=$HOME/.cache/shellllm/llama-server.log}
: ${SHELLLM_EMBED_LOG:=$HOME/.cache/shellllm/llama-embed.log}
# Terminal-context ladder (see below). Defaults to `cmd` — previous
# command + exit status — which is what makes `,,` and "why did that
# fail" work out of the box. Local-first means this never leaves the
# machine unless you point SHELLLM_BASE_URL at a hosted API; set it to
# `off` to disable capture entirely.
: ${SHELLLM_SHELL_CONTEXT:=cmd}

# ─── Tier registry ──────────────────────────────────────────────────────
#
# Each tier maps a name → HF repo + extra llama-server flags + a one-line
# description. Add a tier by appending to all three arrays. `??` looks up
# the gguf inside the HF cache, so the only setup is `huggingface-cli
# download <repo>`.

typeset -gA _SHELLLM_TIER_REPO
typeset -gA _SHELLLM_TIER_ARGS
typeset -gA _SHELLLM_TIER_DESC

_SHELLLM_TIER_REPO[fast]="unsloth/Qwen3.6-35B-A3B-MTP-GGUF"
_SHELLLM_TIER_ARGS[fast]="--spec-type draft-mtp --spec-draft-n-max 3"
_SHELLLM_TIER_DESC[fast]="MoE 3B-active + self-speculative MTP — fastest"

_SHELLLM_TIER_REPO[balanced]="unsloth/Qwen3.6-27B-GGUF"
_SHELLLM_TIER_ARGS[balanced]=""
_SHELLLM_TIER_DESC[balanced]="dense 27B Q4 — solid quality, slower (default)"

_SHELLLM_TIER_REPO[smart]="unsloth/Qwen3-Coder-Next-GGUF"
_SHELLLM_TIER_ARGS[smart]=""
_SHELLLM_TIER_DESC[smart]="latest coder-tuned model — best quality, needs download"

typeset -ga _SHELLLM_TIER_ORDER
_SHELLLM_TIER_ORDER=(fast balanced smart)

# Tier → port. Balanced owns the default port so plain `??` + `?` keep
# their historical behavior; the other tiers get dedicated ports so two
# models can serve side by side and `, --fast` / `? --smart` can route
# a single call to a specific one.
typeset -gA _SHELLLM_TIER_PORT
_SHELLLM_TIER_PORT[fast]="${SHELLLM_PORT_FAST:-8091}"
_SHELLLM_TIER_PORT[balanced]="${SHELLLM_PORT_BALANCED:-$SHELLLM_PORT}"
_SHELLLM_TIER_PORT[smart]="${SHELLLM_PORT_SMART:-8093}"

# Embedding tiers — a separate registry because the model lineage and
# size targets are different (we want something small and fast, not a
# 27B chat model). `?? --start-embed <tier>` resolves against this map.

typeset -gA _SHELLLM_EMBED_REPO
typeset -gA _SHELLLM_EMBED_DESC

_SHELLLM_EMBED_REPO[tiny]="Qwen/Qwen3-Embedding-0.6B-GGUF"
_SHELLLM_EMBED_DESC[tiny]="Qwen3-Embedding 0.6B — same model family as the chat tiers (default)"

_SHELLLM_EMBED_REPO[bge]="ChristianAzinn/bge-small-en-v1.5-gguf"
_SHELLLM_EMBED_DESC[bge]="bge-small-en-v1.5 — tiny English-only, very fast"

_SHELLLM_EMBED_REPO[nomic]="nomic-ai/nomic-embed-text-v1.5-GGUF"
_SHELLLM_EMBED_DESC[nomic]="nomic-embed-text-v1.5 — strong general-purpose retrieval"

typeset -ga _SHELLLM_EMBED_ORDER
_SHELLLM_EMBED_ORDER=(tiny bge nomic)

# ─── terminal context (opt-in privacy ladder) ──────────────────────────
#
# SHELLLM_SHELL_CONTEXT=off|cmd|history|output   (default: cmd)
#
#   off       nothing captured
#   cmd       previous command + exit status (default)
#   history   + last 10 commands
#   output    + recent pane output (tmux only)
#
# Captured values are passed as per-invocation environment — nothing is
# exported into the shell, and the Python side re-checks the level and
# redacts secret-shaped strings before anything reaches the model.
#
# Previous command is read from zsh's `$history` array — robust across
# HIST_IGNORE_DUPS / SHARE_HISTORY, no preexec hook needed. Exit status
# of the previous command is captured by a precmd hook, since `$?` is
# only meaningful inside a hook that runs immediately after the command.
typeset -g _SHELLLM_PREV_STATUS=0

function _shellllm_precmd() {
  _SHELLLM_PREV_STATUS=$?
}

autoload -Uz add-zsh-hook
add-zsh-hook precmd _shellllm_precmd

# Resolve the previous command from history at call time, inside the
# `,` / `,,` / `?` functions. `$HISTCMD` is the slot for the CURRENT
# command (the one that invoked this function), so the previous user
# command is at `$HISTCMD - 1`.
function _shellllm_prev_cmd() {
  local prev="$history[$((HISTCMD-1))]"
  print -- "${prev#"${prev%%[![:space:]]*}"}"
}

function _shellllm_with_ctx() {
  local last_status=$1; shift
  local level="${SHELLLM_SHELL_CONTEXT:-off}"
  if [[ $level != cmd && $level != history && $level != output ]]; then
    "$@"
    return $?
  fi
  local last_cmd="$(_shellllm_prev_cmd)"
  local hist="" out=""
  if [[ $level == history || $level == output ]]; then
    hist="$(builtin fc -ln -11 -2 2>/dev/null)"
  fi
  if [[ $level == output && -n ${TMUX:-} ]]; then
    out="$(command tmux capture-pane -p -S -60 2>/dev/null)"
  fi
  # The level rides along explicitly: it may be a plain (unexported)
  # shell variable, and the Python side re-checks it from the env.
  SHELLLM_SHELL_CONTEXT="$level" \
  SHELLLM_LAST_STATUS="$last_status" \
  SHELLLM_LAST_CMD="$last_cmd" \
  SHELLLM_RECENT_HISTORY="$hist" \
  SHELLLM_PANE_OUTPUT="$out" \
  "$@"
}

# ─── lazy start ─────────────────────────────────────────────────────────
#
# SHELLLM_AUTOSTART=1 makes `,` / `?` bring the default tier up on demand
# instead of erroring when the server is down. Off by default: starting
# a multi-GB model is a deliberate act, and the first start can take a
# minute. The health probe only runs when the feature is on.
function _shellllm_autostart() {
  [[ "${SHELLLM_AUTOSTART:-0}" == 1 ]] || return 0
  _shellllm_server_up && return 0
  print -u2 -- "shellllm: llama-server is down — autostarting (SHELLLM_AUTOSTART=1)"
  _shellllm_start >&2
}

# ─── per-call model routing ─────────────────────────────────────────────
#
# `, --fast …` / `? --smart …` send one call to a specific tier's server
# (see _SHELLLM_TIER_PORT). The flag is consumed here; the Python CLI
# never sees it — it just gets SHELLLM_BASE_URL pointed at the right
# port. The tier must already be up: `?? --start fast`.
#
# Only the FIRST argument routes, so prose mentioning --fast later in a
# prompt (`? what does --fast do in pip`) passes through untouched.
#
# Outputs via globals (zsh has no multi-value returns):
#   _shellllm_route_url    base URL override, or "" for the default
#   _shellllm_route_args   remaining args with the tier flag removed
typeset -g _shellllm_route_url
typeset -ga _shellllm_route_args

function _shellllm_route() {
  _shellllm_route_url=""
  _shellllm_route_args=("$@")
  local tier=""
  case "${1:-}" in
    --fast|--balanced|--smart) tier="${1#--}"; shift; _shellllm_route_args=("$@") ;;
    *) return 0 ;;
  esac
  local port="${_SHELLLM_TIER_PORT[$tier]}"
  if ! _shellllm_server_up "$port"; then
    print -u2 -- "shellllm: tier '$tier' isn't running on :${port}"
    print -u2 -- "  what to do:  ?? --start $tier"
    return 1
  fi
  _shellllm_route_url="http://127.0.0.1:${port}"
}

# ─── `,` — propose, never execute. Lands on the prompt line via print -z.
function _shellllm_comma_run() {
  local last_status=$1; shift
  _shellllm_route "$@" || return $?
  set -- "${_shellllm_route_args[@]}"
  [[ -n $_shellllm_route_url ]] && local -x SHELLLM_BASE_URL=$_shellllm_route_url
  [[ -z $_shellllm_route_url ]] && { _shellllm_autostart || return $?; }
  local cmd
  cmd=$(_shellllm_with_ctx $last_status ${=SHELLLM_COMMA} "$@") || return $?
  [[ -n $cmd ]] && print -z -- "$cmd"
}

function ,() {
  _shellllm_comma_run $_SHELLLM_PREV_STATUS "$@"
}

# ─── `,,` — the comma that knows what just happened. Doubling the glyph
# brings the terminal context along (`, --ctx`); plain `,` stays
# context-free. Bare `,,` with no prompt means "fix the previous
# command" (`, --fix`). The ladder level (SHELLLM_SHELL_CONTEXT,
# default `cmd`) controls how much context rides along.
function ,,() {
  local __last_status=$_SHELLLM_PREV_STATUS
  # `,,` is always fix mode. A leading tier flag is routing, not the
  # prompt; `--pick` (anywhere) opts into the picker; everything else
  # becomes "intent" — a hint the model uses to narrow the repair.
  local -a tier pick rest
  if [[ "${1:-}" == --fast || "${1:-}" == --balanced || "${1:-}" == --smart ]]; then
    tier=("$1"); shift
  fi
  local a
  for a in "$@"; do
    case "$a" in
      --pick) pick=(--pick) ;;
      *)      rest+=("$a") ;;
    esac
  done
  _shellllm_comma_run $__last_status ${tier[@]} --fix ${pick[@]} ${rest[@]}
}

# ─── `?` — answer. `noglob` is required because `?` is a zsh glob char.
function _shellllm_ask_fn() {
  local __last_status=$_SHELLLM_PREV_STATUS
  _shellllm_route "$@" || return $?
  set -- "${_shellllm_route_args[@]}"
  [[ -n $_shellllm_route_url ]] && local -x SHELLLM_BASE_URL=$_shellllm_route_url
  [[ -z $_shellllm_route_url ]] && { _shellllm_autostart || return $?; }
  _shellllm_with_ctx $__last_status ${=SHELLLM_ASK} "$@"
}
alias '?'='noglob _shellllm_ask_fn'

# ─── `???` — memory layer: long-term facts + cross-session recall.
#
#   ??? <q>             bare query → search archived sessions
#   ??? --add <fact>    pin a long-term fact
#   ??? --list          list facts
#   ??? --drop <n>      drop fact #n
#   ??? --status        counts
#   ??? --ask <q>       recall only `?` sessions
#   ??? --comma <q>     recall only `,` sessions
#   ??? --help          usage
#
# Every operation other than bare-query recall is a flag — no
# bare-word verbs. `noglob` is needed because `?` is a zsh glob char;
# aliasing `???` directly is fine because alias expansion runs before
# globbing.
function _shellllm_recall_fn() {
  ${=SHELLLM_RECALL} "$@"
}
alias '???'='noglob _shellllm_recall_fn'

# ─── server helpers ─────────────────────────────────────────────────────

function _shellllm_find_gguf() {
  local repo="$1"
  find "$HOME/.cache/huggingface/hub/models--${repo//\//--}" \
    -name "*.gguf" ! -name "mmproj*" 2>/dev/null | head -1
}

function _shellllm_server_up() {
  local port="${1:-$SHELLLM_PORT}"
  curl -fsS -m 1 "http://127.0.0.1:${port}/health" >/dev/null 2>&1
}

# ─── `?? --doctor` — paste-into-issue health check ──────────────────────
#
# Output is intentionally plain ASCII (no Unicode, no colour) so it
# round-trips through GitHub issue bodies, Slack pastes, and email
# verbatim. Every check is read-only — we never start/stop anything
# during a doctor run.
function _shellllm_doctor() {
  local _CHECK="[ok]" _CROSS="[--]" _WARN="[!!]" _DASH="  ·  "
  local exit_code=0

  print -- "shellllm doctor — $(date '+%Y-%m-%d %H:%M:%S %Z')"
  print -- ""

  # ── platform basics ──
  print -- "platform:"
  printf '  uname  : %s\n' "$(uname -srm 2>/dev/null)"
  printf '  shell  : %s (%s)\n' "${ZSH_VERSION:-?}" "${SHELL:-?}"
  print -- ""

  # ── binaries on PATH ──
  # Required: comma/ask/recall (the CLIs), llama-server, fzf (picker
  # falls back to stdin but the UX drops noticeably).
  # Optional: huggingface-cli (only needed to download new models; if
  # all tiers are already cached, you never call it again).
  print -- "binaries on \$PATH:"
  local b
  for b in shellllm-comma shellllm-ask shellllm-recall llama-server fzf; do
    if command -v "$b" >/dev/null 2>&1; then
      printf '  %s %-18s %s\n' "$_CHECK" "$b" "$(command -v "$b")"
    else
      printf '  %s %-18s not found\n' "$_CROSS" "$b"
      exit_code=1
    fi
  done
  if command -v huggingface-cli >/dev/null 2>&1; then
    printf '  %s %-18s %s\n' "$_CHECK" "huggingface-cli" "$(command -v huggingface-cli)"
  else
    printf '  %s %-18s optional (only needed to download new tier models)\n' "$_DASH" "huggingface-cli"
  fi
  print -- ""

  # ── zsh layer ──
  print -- "zsh layer:"
  if typeset -f _shellllm_start >/dev/null 2>&1; then
    printf '  %s shellllm.zsh sourced\n' "$_CHECK"
  else
    printf '  %s shellllm.zsh NOT sourced — add to ~/.zshrc:\n' "$_CROSS"
    print -- "         source \"\$(brew --prefix)/share/shellllm/shellllm.zsh\""
    exit_code=1
  fi
  printf '  SHELLLM_SHELL_CONTEXT  : %s\n' "${SHELLLM_SHELL_CONTEXT:-(unset)}"
  printf '  SHELLLM_BASE_URL       : %s\n' "${SHELLLM_BASE_URL:-(default — http://127.0.0.1:${SHELLLM_PORT})}"
  printf '  SHELLLM_AUTOSTART      : %s\n' "${SHELLLM_AUTOSTART:-(unset)}"
  print -- ""

  # ── llama-server tiers ──
  print -- "servers:"
  local any_up=0 t p name
  for t in "${_SHELLLM_TIER_ORDER[@]}"; do
    p="${_SHELLLM_TIER_PORT[$t]}"
    if _shellllm_server_up "$p"; then
      name=$(_shellllm_running_model "$p")
      printf '  %s %-8s :%-5s %s%s\n' "$_CHECK" "$t" "$p" "${name:-?}" ""
      any_up=1
    else
      printf '  %s %-8s :%-5s down\n' "$_CROSS" "$t" "$p"
    fi
  done
  if _shellllm_embed_up; then
    printf '  %s embed    :%-5s up\n' "$_CHECK" "$SHELLLM_EMBED_PORT"
  else
    printf '  %s embed    :%-5s down (optional — needed only for semantic recall)\n' "$_DASH" "$SHELLLM_EMBED_PORT"
  fi
  if (( ! any_up )); then
    printf '  %s no chat tier is up — start one with `?? --start fast`\n' "$_WARN"
    exit_code=1
  fi
  print -- ""

  # ── archive + memory ──
  print -- "archive + memory:"
  if command -v shellllm-recall >/dev/null 2>&1; then
    local status_line
    status_line=$(shellllm-recall --status 2>/dev/null)
    if [[ -n $status_line ]]; then
      printf '  %s %s\n' "$_CHECK" "$status_line"
    else
      printf '  %s archive unreachable\n' "$_CROSS"
      exit_code=1
    fi
  else
    printf '  %s shellllm-recall missing — skipping\n' "$_CROSS"
  fi
  print -- ""

  # ── tier models on disk ──
  print -- "tier models in HF cache:"
  for t in "${_SHELLLM_TIER_ORDER[@]}"; do
    local repo="${_SHELLLM_TIER_REPO[$t]}"
    local gguf=$(_shellllm_find_gguf "$repo")
    if [[ -f $gguf ]]; then
      printf '  %s %-8s %s\n' "$_CHECK" "$t" "$repo"
    else
      printf '  %s %-8s %s (not downloaded)\n' "$_DASH" "$t" "$repo"
    fi
  done
  print -- ""

  if (( exit_code == 0 )); then
    print -- "all checks passed."
  else
    print -- "some checks failed. paste this output into an issue if you're stuck:"
    print -- "  https://github.com/FrancoisChastel/shellllm/issues/new"
  fi
  return $exit_code
}

# Returns the GGUF filename of whatever model is currently loaded on
# the given port (via llama-server's OpenAI-shape /v1/models endpoint),
# stripped of the trailing `.gguf`. Falls back to "" on any error so
# the caller can decide how to render the gap.
function _shellllm_running_model() {
  local port="${1:-$SHELLLM_PORT}"
  local body
  body=$(curl -fsS -m 2 "http://127.0.0.1:${port}/v1/models" 2>/dev/null) || return 0
  # Prefer .data[0].id (OpenAI shape); fall back to .models[0].name
  # (llama-server's older field). Plain sed beats jq for portability.
  local raw
  raw=$(printf '%s' "$body" | sed -n 's/.*"data"[^[]*\[[^{]*{[^"]*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  if [[ -z $raw ]]; then
    raw=$(printf '%s' "$body" | sed -n 's/.*"models"[^[]*\[[^{]*{[^"]*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  fi
  printf '%s' "${raw%.gguf}"
}

function _shellllm_embed_up() {
  curl -fsS -m 1 "http://127.0.0.1:${SHELLLM_EMBED_PORT}/health" >/dev/null 2>&1
}

function _shellllm_list_embed_tiers() {
  local _G=$'\e[32m' _D=$'\e[2m' _R=$'\e[31m' _C=$'\e[36m' _N=$'\e[0m'
  print -- "shellllm embedding tiers (use ${_C}?? --start-embed <tier>${_N}):"
  print
  local tier repo desc gguf
  for tier in "${_SHELLLM_EMBED_ORDER[@]}"; do
    repo="${_SHELLLM_EMBED_REPO[$tier]}"
    desc="${_SHELLLM_EMBED_DESC[$tier]}"
    gguf=$(_shellllm_find_gguf "$repo")
    if [[ -f $gguf ]]; then
      print -- "  ${_G}✓${_N} ${_C}${tier}${_N}   $desc"
    else
      print -- "  ${_R}✗${_N} ${_C}${tier}${_N}   $desc"
      print -- "      ${_D}huggingface-cli download $repo${_N}"
    fi
  done
}

function _shellllm_list_tiers() {
  local _G=$'\e[32m' _D=$'\e[2m' _R=$'\e[31m' _C=$'\e[36m' _N=$'\e[0m'
  print -- "shellllm tiers (use ${_C}?? --start <tier>${_N}):"
  print
  local tier repo desc gguf
  for tier in "${_SHELLLM_TIER_ORDER[@]}"; do
    repo="${_SHELLLM_TIER_REPO[$tier]}"
    desc="${_SHELLLM_TIER_DESC[$tier]}"
    gguf=$(_shellllm_find_gguf "$repo")
    if [[ -f $gguf ]]; then
      print -- "  ${_G}✓${_N} ${_C}${tier}${_N} ${_D}:${_SHELLLM_TIER_PORT[$tier]}${_N}   $desc"
    else
      print -- "  ${_R}✗${_N} ${_C}${tier}${_N} ${_D}:${_SHELLLM_TIER_PORT[$tier]}${_N}   $desc"
      print -- "      ${_D}huggingface-cli download $repo${_N}"
    fi
  done
}

# ─── `??` — start (or stop / list / status) the local llama-server.
#
#   ??                       start the default tier (balanced)
#   ?? --start fast          start a tier on its own port (tiers can
#   ?? --start smart         run side by side; `, --fast` etc. routes)
#   ?? --model PATH          start with an explicit gguf
#   ?? --list                show tiers, ports, and which are downloaded
#   ?? --status              which tiers/servers are up
#   ?? --stop [tier]         kill the default server, or one tier's
#
# If a tier isn't downloaded, you get a copy-pasteable
# `huggingface-cli download` line.

function _shellllm_start_embed() {
  local tier="${1:-tiny}"
  local repo="${_SHELLLM_EMBED_REPO[$tier]:-}"
  if [[ -z $repo ]]; then
    print -u2 -- "?? unknown embedding tier: $tier"
    _shellllm_list_embed_tiers >&2
    return 1
  fi
  if _shellllm_embed_up; then
    echo "embedding server already running on :${SHELLLM_EMBED_PORT}"
    return 0
  fi
  local model
  model=$(_shellllm_find_gguf "$repo")
  if [[ ! -f $model ]]; then
    print -u2 -- "?? embedding tier '$tier' isn't downloaded."
    print -u2 -- "  what to do:"
    print -u2 -- "    huggingface-cli download $repo"
    return 1
  fi
  if ! command -v llama-server >/dev/null 2>&1; then
    print -u2 -- "?? llama-server binary not on \$PATH."
    print -u2 -- "  what to do:  brew install llama.cpp"
    return 1
  fi

  mkdir -p "$(dirname "$SHELLLM_EMBED_LOG")"
  echo "starting embedding server"
  echo "  tier  : $tier"
  echo "  model : $model"
  echo "  log   : $SHELLLM_EMBED_LOG"

  nohup llama-server \
    -m "$model" \
    -c "$SHELLLM_EMBED_CTX" \
    -ngl "$SHELLLM_NGL" \
    --host 127.0.0.1 \
    --port "$SHELLLM_EMBED_PORT" \
    --embedding \
    --pooling mean \
    >"$SHELLLM_EMBED_LOG" 2>&1 &
  disown

  printf "  waiting"
  local i
  for i in {1..60}; do
    if _shellllm_embed_up; then
      echo " ready (${i}s)"
      return 0
    fi
    printf "."
    sleep 1
  done

  print -u2 -- ""
  print -u2 -- "?? embedding server not ready after 60s. what to do:"
  print -u2 -- "  1. tail -50 $SHELLLM_EMBED_LOG"
  print -u2 -- "  2. lsof -iTCP:${SHELLLM_EMBED_PORT} -sTCP:LISTEN"
  return 1
}

function _shellllm_start() {
  local tier=""
  local model=""

  while (( $# )); do
    case "$1" in
      --start|-s) tier="$2"; shift 2 ;;
      --model|-m) model="$2"; shift 2 ;;
      --start-embed)
        _shellllm_start_embed "${2:-tiny}"
        return $? ;;
      --stop-embed)
        pkill -f "llama-server.*--port ${SHELLLM_EMBED_PORT}" \
          && echo "stopped embedding server" || echo "(no embedding server to stop)"
        return 0 ;;
      --status-embed)
        if _shellllm_embed_up; then
          echo "embed up   → http://127.0.0.1:${SHELLLM_EMBED_PORT}"
        else
          echo "embed down → run ?? --start-embed to start"
        fi
        return 0 ;;
      --list-embed) _shellllm_list_embed_tiers; return 0 ;;
      --stop)
        local stop_port="$SHELLLM_PORT" stop_what="default"
        if [[ -n "${2:-}" && -n "${_SHELLLM_TIER_PORT[${2:-_}]:-}" ]]; then
          stop_port="${_SHELLLM_TIER_PORT[$2]}"
          stop_what="$2"
        fi
        pkill -f "llama-server.*--port ${stop_port}" \
          && echo "stopped ${stop_what} (:${stop_port})" \
          || echo "(nothing to stop on :${stop_port})"
        return 0 ;;
      --status)
        local t p name any=0
        for t in "${_SHELLLM_TIER_ORDER[@]}"; do
          p="${_SHELLLM_TIER_PORT[$t]}"
          if _shellllm_server_up "$p"; then
            name=$(_shellllm_running_model "$p")
            echo "up   ${t} → ${name:-?}  ·  http://127.0.0.1:${p}"
            any=1
          fi
        done
        if (( ! any )); then
          if _shellllm_server_up; then
            name=$(_shellllm_running_model "$SHELLLM_PORT")
            echo "up   → ${name:-?}  ·  http://127.0.0.1:${SHELLLM_PORT}"
          else
            echo "down → run ?? to start"
          fi
        fi
        return 0 ;;
      --list|-l) _shellllm_list_tiers; return 0 ;;
      --doctor) _shellllm_doctor; return $? ;;
      --help|-h)
        print -- "?? — start the local llama-server"
        print -- "  ?? [--start <tier>] [--model PATH]"
        print -- "  ?? --list | --status | --stop [tier]"
        print -- "  ?? --start-embed <tier> | --status-embed | --stop-embed | --list-embed"
        print -- "  ?? --doctor               health check (paste into an issue)"
        print -- "  per-call routing: , --fast …  /  ? --smart …  (tier must be up)"
        _shellllm_list_tiers
        print
        _shellllm_list_embed_tiers
        return 0 ;;
      *) print -u2 -- "unknown arg: $1 (try: ?? --help)"; return 2 ;;
    esac
  done

  # Validate the tier name before doing anything else.
  if [[ -n $tier && -z "${_SHELLLM_TIER_REPO[$tier]:-}" ]]; then
    print -u2 -- "?? unknown tier: $tier"
    _shellllm_list_tiers >&2
    return 1
  fi

  local extra_args=""
  if [[ -n $model ]]; then
    :  # explicit model, no tier args
  elif [[ -n $tier ]]; then
    local repo="${_SHELLLM_TIER_REPO[$tier]}"
    model=$(_shellllm_find_gguf "$repo")
    extra_args="${_SHELLLM_TIER_ARGS[$tier]:-}"
    if [[ ! -f $model ]]; then
      print -u2 -- "?? tier '$tier' isn't downloaded yet."
      print -u2 -- "  what to do:"
      print -u2 -- "    huggingface-cli download $repo"
      print -u2 -- "  or pick a tier that's ready:"
      _shellllm_list_tiers >&2
      return 1
    fi
  elif [[ -n ${SHELLLM_LLAMA_MODEL:-} ]]; then
    model="$SHELLLM_LLAMA_MODEL"
  else
    # No tier, no --model, no env override → default to balanced.
    tier="balanced"
    local repo="${_SHELLLM_TIER_REPO[balanced]}"
    model=$(_shellllm_find_gguf "$repo")
    extra_args="${_SHELLLM_TIER_ARGS[balanced]}"
    if [[ ! -f $model ]]; then
      print -u2 -- "?? no model available. what to do:"
      print -u2 -- "  1. ?? --list           show tiers"
      print -u2 -- "  2. ?? --start fast     if a tier is ready"
      print -u2 -- "  3. huggingface-cli download $repo"
      return 1
    fi
  fi

  if ! command -v llama-server >/dev/null 2>&1; then
    print -u2 -- "?? llama-server binary not on \$PATH."
    print -u2 -- "  what to do:  brew install llama.cpp"
    return 1
  fi

  # Tiers bind to their own ports so several can serve side by side
  # (`, --fast` / `? --smart` route per call). Explicit --model and env
  # fallbacks stay on the default port.
  local port="$SHELLLM_PORT"
  [[ -n $tier ]] && port="${_SHELLLM_TIER_PORT[$tier]}"

  if _shellllm_server_up "$port"; then
    echo "llama-server already running on :${port}${tier:+ (tier $tier)}"
    echo "  to replace it:  ?? --stop${tier:+ $tier} && ?? --start ${tier:-…}"
    return 0
  fi

  local log="$SHELLLM_LOG"
  [[ "$port" != "$SHELLLM_PORT" ]] && log="${SHELLLM_LOG}.${port}"

  mkdir -p "$(dirname "$log")"
  echo "starting llama-server"
  [[ -n $tier ]] && echo "  tier  : $tier"
  echo "  model : $model"
  echo "  port  : $port"
  [[ -n $extra_args ]] && echo "  extra : $extra_args"
  echo "  log   : $log"

  nohup llama-server \
    -m "$model" \
    -c "$SHELLLM_CTX" \
    -ngl "$SHELLLM_NGL" \
    --host 127.0.0.1 \
    --port "$port" \
    ${=extra_args} \
    >"$log" 2>&1 &
  disown

  printf "  waiting"
  local i
  for i in {1..120}; do
    if _shellllm_server_up "$port"; then
      echo " ready (${i}s)"
      return 0
    fi
    printf "."
    sleep 1
  done

  print -u2 -- ""
  print -u2 -- "?? still not ready after 120s. what to do:"
  print -u2 -- "  1. tail -50 $log"
  print -u2 -- "  2. lsof -iTCP:${port} -sTCP:LISTEN"
  print -u2 -- "  3. if log says 'unknown option --spec-type': brew upgrade llama.cpp"
  return 1
}

alias '??'='noglob _shellllm_start'
