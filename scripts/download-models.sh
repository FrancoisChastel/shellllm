#!/usr/bin/env bash
# download-models.sh — fetch the GGUF tiers shellllm knows how to run.
#
# Usage:
#   scripts/download-models.sh                  # default: balanced (~16 GB)
#   scripts/download-models.sh balanced         # same
#   scripts/download-models.sh fast smart       # multiple tiers
#   scripts/download-models.sh all              # every chat tier
#   scripts/download-models.sh --with-embed     # also fetch the default embed tier
#   scripts/download-models.sh --list           # show tiers + on-disk status
#
# Requires `huggingface-cli` (`pipx install huggingface_hub` if missing).
# Models land in ~/.cache/huggingface, where `shellllm-comma` / `shellllm-ask`
# discover them automatically.
#
# Plays nicely with cold installs: re-downloads are skipped (HF cache is
# content-addressed), so this script is safe to re-run.

set -euo pipefail

# ─── Tier registry ─────────────────────────────────────────────────────
# Keep these in sync with zsh/shellllm.zsh. Listed flat so the script
# stays portable across bash 3.2 (macOS default) and bash 4+.
CHAT_TIERS=(
  "fast=unsloth/Qwen3.6-35B-A3B-MTP-GGUF|MoE 3B-active + MTP — fastest on Apple Silicon (~18 GB)"
  "balanced=unsloth/Qwen3.6-27B-GGUF|dense 27B Q4 — solid quality, slower (~16 GB, default)"
  "smart=unsloth/Qwen3-Coder-Next-GGUF|coder-tuned — best for shell/agent work (~17 GB)"
)
EMBED_TIERS=(
  "tiny=Qwen/Qwen3-Embedding-0.6B-GGUF|same model family as chat tiers (default, ~500 MB)"
  "bge=ChristianAzinn/bge-small-en-v1.5-gguf|tiny English-only (~70 MB)"
  "nomic=nomic-ai/nomic-embed-text-v1.5-GGUF|strong general-purpose (~270 MB)"
)
DEFAULT_TIER="balanced"
DEFAULT_EMBED="tiny"

# ─── pretty output ─────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN=$'\e[32m'; DIM=$'\e[2m'; CYAN=$'\e[36m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
else
  GREEN=""; DIM=""; CYAN=""; RED=""; YELLOW=""; BOLD=""; RESET=""
fi

_tier_lookup() {
  # _tier_lookup <name> <"chat"|"embed">  → echoes "repo|desc" or empty
  local name=$1 kind=$2 entry
  local -a registry
  if [[ $kind == embed ]]; then registry=("${EMBED_TIERS[@]}"); else registry=("${CHAT_TIERS[@]}"); fi
  for entry in "${registry[@]}"; do
    if [[ ${entry%%=*} == "$name" ]]; then
      printf '%s' "${entry#*=}"
      return 0
    fi
  done
  return 1
}

_cache_path() {
  # HF stores models at hub/models--<org>--<name>/, sluggified.
  local repo=$1
  echo "${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${repo//\//--}"
}

_have_gguf() {
  # Any non-mmproj .gguf in the cache means "downloaded".
  local repo=$1
  find "$(_cache_path "$repo")" -name "*.gguf" ! -name "mmproj*" -print -quit 2>/dev/null | grep -q .
}

print_list() {
  local entry name rest repo desc tag check
  printf '\n%s%s%s\n\n' "$BOLD" "chat tiers" "$RESET"
  for entry in "${CHAT_TIERS[@]}"; do
    name=${entry%%=*}; rest=${entry#*=}; repo=${rest%%|*}; desc=${rest#*|}
    if _have_gguf "$repo"; then check="${GREEN}✓${RESET}"; else check="${RED}✗${RESET}"; fi
    tag=""; [[ $name == "$DEFAULT_TIER" ]] && tag=" ${DIM}(default)${RESET}"
    printf '  %s %s%-10s%s %s%s\n' "$check" "$CYAN" "$name" "$RESET" "$desc" "$tag"
    printf '      %s%s%s\n' "$DIM" "$repo" "$RESET"
  done
  printf '\n%s%s%s\n\n' "$BOLD" "embedding tiers" "$RESET"
  for entry in "${EMBED_TIERS[@]}"; do
    name=${entry%%=*}; rest=${entry#*=}; repo=${rest%%|*}; desc=${rest#*|}
    if _have_gguf "$repo"; then check="${GREEN}✓${RESET}"; else check="${RED}✗${RESET}"; fi
    tag=""; [[ $name == "$DEFAULT_EMBED" ]] && tag=" ${DIM}(default)${RESET}"
    printf '  %s %s%-10s%s %s%s\n' "$check" "$CYAN" "$name" "$RESET" "$desc" "$tag"
    printf '      %s%s%s\n' "$DIM" "$repo" "$RESET"
  done
  printf '\n'
}

print_help() {
  cat <<EOF
${BOLD}usage:${RESET} $(basename "$0") [tier ...] [--with-embed[=NAME]] [--list] [--help]

Tiers (chat): fast, balanced (default), smart, all
Tiers (embed via --with-embed=NAME): tiny (default), bge, nomic

Examples:
  $(basename "$0")                          # just balanced
  $(basename "$0") fast                     # one tier
  $(basename "$0") fast balanced smart      # all three chat tiers
  $(basename "$0") all --with-embed         # everything + default embed
  $(basename "$0") --with-embed=nomic       # default chat tier + nomic embed
  $(basename "$0") --list                   # show what's downloaded

EOF
}

# ─── arg parsing ───────────────────────────────────────────────────────
WANT_LIST=0
WANT_EMBED=""        # empty=none, "_default" or explicit name
declare -a WANT_CHAT
for arg in "$@"; do
  case "$arg" in
    -h|--help) print_help; exit 0 ;;
    --list)    WANT_LIST=1 ;;
    --with-embed) WANT_EMBED="_default" ;;
    --with-embed=*) WANT_EMBED="${arg#--with-embed=}" ;;
    all) WANT_CHAT+=(fast balanced smart) ;;
    fast|balanced|smart) WANT_CHAT+=("$arg") ;;
    *)
      printf '%s%s%s unknown argument: %s\n\n' "$RED" "error:" "$RESET" "$arg" >&2
      print_help >&2
      exit 2
      ;;
  esac
done

if (( WANT_LIST )); then
  print_list
  exit 0
fi

# Default to the balanced chat tier if no tiers were requested.
if (( ${#WANT_CHAT[@]} == 0 )) && [[ -z $WANT_EMBED ]]; then
  WANT_CHAT=("$DEFAULT_TIER")
fi

if ! command -v huggingface-cli >/dev/null 2>&1; then
  cat >&2 <<EOF
${RED}error:${RESET} huggingface-cli not found on \$PATH.
  pipx install huggingface_hub
  # or
  pip install --user huggingface_hub
EOF
  exit 1
fi

# ─── dedup + download ──────────────────────────────────────────────────
download() {
  local kind=$1 tier=$2 entry repo desc
  if ! entry=$(_tier_lookup "$tier" "$kind"); then
    printf '%s%s%s unknown %s tier: %s\n' "$RED" "error:" "$RESET" "$kind" "$tier" >&2
    return 1
  fi
  repo=${entry%%|*}
  desc=${entry#*|}
  printf '\n%s▼%s %s (%s)\n' "$CYAN" "$RESET" "$BOLD$tier$RESET" "$desc"
  printf '   %s%s%s\n' "$DIM" "$repo" "$RESET"
  if _have_gguf "$repo"; then
    printf '   %s✓%s already in cache — skipping.\n' "$GREEN" "$RESET"
    return 0
  fi
  huggingface-cli download "$repo"
}

# de-dup the chat list while preserving order
declare -a SEEN
in_list() { local x=$1; shift; for s in "$@"; do [[ $s == "$x" ]] && return 0; done; return 1; }

declare -a UNIQUE_CHAT
for t in "${WANT_CHAT[@]+"${WANT_CHAT[@]}"}"; do
  in_list "$t" "${UNIQUE_CHAT[@]+"${UNIQUE_CHAT[@]}"}" || UNIQUE_CHAT+=("$t")
done

for t in "${UNIQUE_CHAT[@]+"${UNIQUE_CHAT[@]}"}"; do
  download chat "$t"
done

if [[ -n $WANT_EMBED ]]; then
  e=$WANT_EMBED
  [[ $e == "_default" ]] && e=$DEFAULT_EMBED
  download embed "$e"
fi

printf '\n%s✓%s done. start the server: %s??%s (or %s?? --start <tier>%s).\n' \
  "$GREEN" "$RESET" "$CYAN" "$RESET" "$CYAN" "$RESET"
