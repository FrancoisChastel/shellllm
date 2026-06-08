# shellllm — source this from .zshrc
#
#   source /path/to/shellllm/zsh/shellllm.zsh
#
# Assumes `shellllm-comma` and `shellllm-ask` are on $PATH (pip-installed,
# or the project venv's bin/ is on PATH).

: ${SHELLLM_COMMA:=shellllm-comma}
: ${SHELLLM_ASK:=shellllm-ask}
: ${SHELLLM_SEARCH:=shellllm-search}
: ${SHELLLM_PORT:=8080}
: ${SHELLLM_EMBED_PORT:=8081}
: ${SHELLLM_EMBED_CTX:=2048}
: ${SHELLLM_NGL:=99}
: ${SHELLLM_CTX:=32768}
: ${SHELLLM_LOG:=$HOME/.cache/shellllm/llama-server.log}
: ${SHELLLM_EMBED_LOG:=$HOME/.cache/shellllm/llama-embed.log}

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

# ─── `,` — propose, never execute. Lands on the prompt line via print -z.
function ,() {
  local cmd
  cmd=$(${=SHELLLM_COMMA} "$@") || return $?
  [[ -n $cmd ]] && print -z -- "$cmd"
}

# ─── `?` — answer. `noglob` is required because `?` is a zsh glob char.
function _shellllm_ask_fn() {
  ${=SHELLLM_ASK} "$@"
}
alias '?'='noglob _shellllm_ask_fn'

# ─── `???` — answer by searching the web first. Same noglob requirement.
function _shellllm_search_fn() {
  ${=SHELLLM_SEARCH} "$@"
}
alias '???'='noglob _shellllm_search_fn'

# ─── server helpers ─────────────────────────────────────────────────────

function _shellllm_find_gguf() {
  local repo="$1"
  find "$HOME/.cache/huggingface/hub/models--${repo//\//--}" \
    -name "*.gguf" ! -name "mmproj*" 2>/dev/null | head -1
}

function _shellllm_server_up() {
  curl -fsS -m 1 "http://127.0.0.1:${SHELLLM_PORT}/health" >/dev/null 2>&1
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
      print -- "  ${_G}✓${_N} ${_C}${tier}${_N}   $desc"
    else
      print -- "  ${_R}✗${_N} ${_C}${tier}${_N}   $desc"
      print -- "      ${_D}huggingface-cli download $repo${_N}"
    fi
  done
}

# ─── `??` — start (or stop / list / status) the local llama-server.
#
#   ??                       start the default tier (balanced)
#   ?? --start fast          start a specific tier
#   ?? --start balanced
#   ?? --start smart
#   ?? --model PATH          start with an explicit gguf
#   ?? --list                show tiers and which are downloaded
#   ?? --status              up/down
#   ?? --stop                kill the server
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
        pkill -f "llama-server.*--port ${SHELLLM_PORT}" \
          && echo "stopped" || echo "(nothing to stop)"
        return 0 ;;
      --status)
        if _shellllm_server_up; then
          echo "up   → http://127.0.0.1:${SHELLLM_PORT}"
        else
          echo "down → run ?? to start"
        fi
        return 0 ;;
      --list|-l) _shellllm_list_tiers; return 0 ;;
      --help|-h)
        print -- "?? — start the local llama-server"
        print -- "  ?? [--start <tier>] [--model PATH]"
        print -- "  ?? --list | --status | --stop"
        print -- "  ?? --start-embed <tier> | --status-embed | --stop-embed | --list-embed"
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

  if _shellllm_server_up; then
    if [[ -n $tier || -n $model ]]; then
      print -u2 -- "?? llama-server is already up. To switch:"
      print -u2 -- "  ?? --stop && ?? --start ${tier:-…}"
      return 1
    fi
    echo "llama-server already running on :${SHELLLM_PORT}"
    return 0
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

  mkdir -p "$(dirname "$SHELLLM_LOG")"
  echo "starting llama-server"
  [[ -n $tier ]] && echo "  tier  : $tier"
  echo "  model : $model"
  [[ -n $extra_args ]] && echo "  extra : $extra_args"
  echo "  log   : $SHELLLM_LOG"

  nohup llama-server \
    -m "$model" \
    -c "$SHELLLM_CTX" \
    -ngl "$SHELLLM_NGL" \
    --host 127.0.0.1 \
    --port "$SHELLLM_PORT" \
    ${=extra_args} \
    >"$SHELLLM_LOG" 2>&1 &
  disown

  printf "  waiting"
  local i
  for i in {1..120}; do
    if _shellllm_server_up; then
      echo " ready (${i}s)"
      return 0
    fi
    printf "."
    sleep 1
  done

  print -u2 -- ""
  print -u2 -- "?? still not ready after 120s. what to do:"
  print -u2 -- "  1. tail -50 $SHELLLM_LOG"
  print -u2 -- "  2. lsof -iTCP:${SHELLLM_PORT} -sTCP:LISTEN"
  print -u2 -- "  3. if log says 'unknown option --spec-type': brew upgrade llama.cpp"
  return 1
}

alias '??'='noglob _shellllm_start'
