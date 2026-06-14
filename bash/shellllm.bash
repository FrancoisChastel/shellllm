# shellllm — bash adapter
#
# Bash doesn't allow `?`, `??`, `???` as command names (they're globs),
# so this adapter exposes the same Python CLIs under short alphabetic
# aliases. Source from ~/.bashrc:
#
#     source /path/to/shellllm/bash/shellllm.bash
#
# Provides:
#     llmc    propose commands         llmc find five largest files
#     llmcc   propose with context     llmcc verify the file just produced
#     llmf    fix the previous command llmf
#     llma    ask                      llma what does git stash do
#     llmm    memory / recall          llmm --add I prefer ripgrep
#                                      llmm docker volumes
#
# Server control (??) and per-call tier routing (--fast / --smart) are
# zsh-layer features; in bash, start llama-server yourself or call the
# CLIs with SHELLLM_BASE_URL=http://127.0.0.1:8091 to route per call.

: "${SHELLLM_COMMA:=shellllm-comma}"
: "${SHELLLM_ASK:=shellllm-ask}"
: "${SHELLLM_RECALL:=shellllm-recall}"
: "${SHELLLM_SHELL_CONTEXT:=cmd}"
export SHELLLM_SHELL_CONTEXT

# bash-preexec (https://github.com/rcaloras/bash-preexec) is the
# standard way to get reliable "last command + last exit status" in
# bash. If it's loaded we use it; otherwise we fall back to $? at call
# time (still useful) and an empty SHELLLM_LAST_CMD.
_shellllm_preexec() { _SHELLLM_PREV_CMD="$1"; }
_shellllm_precmd()  { _SHELLLM_PREV_STATUS=$?; }
if declare -f precmd_functions preexec_functions >/dev/null 2>&1; then
  preexec_functions+=(_shellllm_preexec)
  precmd_functions+=(_shellllm_precmd)
fi

_shellllm_with_ctx() {
  SHELLLM_LAST_STATUS="${_SHELLLM_PREV_STATUS:-$1}" \
  SHELLLM_LAST_CMD="${_SHELLLM_PREV_CMD:-}" \
  "${@:2}"
}

llmc()  { local s=$?; "$SHELLLM_COMMA" "$@"; }
llmcc() { local s=$?; _shellllm_with_ctx "$s" "$SHELLLM_COMMA" --ctx "$@"; }
llmf()  { local s=$?; _shellllm_with_ctx "$s" "$SHELLLM_COMMA" --fix; }
llma()  { local s=$?; _shellllm_with_ctx "$s" "$SHELLLM_ASK" "$@"; }
llmm()  { "$SHELLLM_RECALL" "$@"; }
