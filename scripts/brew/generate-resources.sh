#!/usr/bin/env bash
# Regenerate the `resource` stanzas in Formula/shellllm.rb.
#
# Why: every PyPI dependency in the formula needs a pinned tarball URL
# plus its SHA-256. Maintaining these by hand drifts; this script asks
# `homebrew-pypi-poet` to compute them from a clean virtualenv that has
# exactly shellllm's runtime deps installed.
#
# Output: stanzas printed to stdout. Copy them into Formula/shellllm.rb
# in place of the existing `resource` blocks (between the comment marker
# and the `def install` line).
#
# Usage:
#   scripts/brew/generate-resources.sh           # print to stdout
#   scripts/brew/generate-resources.sh --in-place  # rewrite Formula/shellllm.rb

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
FORMULA="$ROOT/Formula/shellllm.rb"

command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

python3 -m venv "$TMPDIR/venv"
# shellcheck disable=SC1091
source "$TMPDIR/venv/bin/activate"

pip install --quiet --upgrade pip
# Install shellllm itself (which pulls httpx + rich + their transitive deps)
# plus poet, which inspects the resulting environment.
pip install --quiet "$ROOT"
pip install --quiet homebrew-pypi-poet

STANZAS="$(poet shellllm | sed -n '/^  resource/,/^  end$/p')"

if [[ "${1:-}" == "--in-place" ]]; then
  python3 - "$FORMULA" <<PY
import re, sys
path = sys.argv[1]
src = open(path).read()
stanzas = """$STANZAS"""
new = re.sub(
    r"(  # PyPI resources.*?\n)(  resource .*?\n  end\n\n?)+",
    r"\1" + stanzas.rstrip() + "\n\n",
    src,
    count=1,
    flags=re.S,
)
if new == src:
    sys.exit("error: resource block markers not found in formula")
open(path, "w").write(new)
PY
  echo "rewrote $FORMULA" >&2
else
  echo "$STANZAS"
fi
