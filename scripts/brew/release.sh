#!/usr/bin/env bash
# Cut a release and stamp the formula's main `sha256` with the new tarball hash.
#
# Steps this script does:
#   1. Verifies the working tree is clean.
#   2. Reads the version from pyproject.toml.
#   3. Tags `v<version>` and pushes it (GitHub auto-publishes the tarball).
#   4. Waits for the tarball to be available, then computes its SHA-256.
#   5. Rewrites Formula/shellllm.rb:
#        - bumps `url` to the new version
#        - replaces the main `sha256` with the freshly computed hash
#   6. Reminds you to run scripts/brew/generate-resources.sh if any
#      PyPI dep version changed in pyproject.toml since the last release.
#
# It does NOT push the formula commit or open a PR against the tap repo —
# that part is intentionally manual so you can review the diff.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
FORMULA="$ROOT/Formula/shellllm.rb"
REPO="FrancoisChastel/shellllm"

cd "$ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty; commit or stash first" >&2
  exit 1
fi

VERSION="$(python3 -c "import tomllib, pathlib; \
  print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")"
TAG="v${VERSION}"

echo "==> releasing ${TAG}"

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "tag $TAG already exists locally — skipping tag creation" >&2
else
  git tag -a "$TAG" -m "release $TAG"
  git push origin "$TAG"
fi

TARBALL_URL="https://github.com/${REPO}/archive/refs/tags/${TAG}.tar.gz"
echo "==> fetching $TARBALL_URL"

for _ in 1 2 3 4 5; do
  if curl -fsSLI "$TARBALL_URL" >/dev/null 2>&1; then
    break
  fi
  echo "    not ready yet, retrying in 4s..."
  sleep 4
done

SHA256="$(curl -fsSL "$TARBALL_URL" | shasum -a 256 | awk '{print $1}')"
echo "==> sha256: $SHA256"

python3 - "$FORMULA" "$VERSION" "$SHA256" <<'PY'
import re, sys
path, version, sha = sys.argv[1:]
src = open(path).read()
src = re.sub(
    r'url "https://github\.com/[^"]+/archive/refs/tags/v[^"]+\.tar\.gz"',
    f'url "https://github.com/FrancoisChastel/shellllm/archive/refs/tags/v{version}.tar.gz"',
    src,
    count=1,
)
# Only the FIRST sha256 in the file is the main tarball's; resource hashes
# follow inside resource blocks. We anchor on the line right after `url ...`.
src = re.sub(
    r'(url "https://github\.com/FrancoisChastel/shellllm[^"]+"\n  sha256 ")[^"]+(")',
    rf'\g<1>{sha}\g<2>',
    src,
    count=1,
)
open(path, "w").write(src)
PY

echo "==> Formula/shellllm.rb stamped for $TAG"
echo
echo "next steps:"
echo "  1. if pyproject.toml deps changed since last release:"
echo "       scripts/brew/generate-resources.sh --in-place"
echo "  2. commit the formula bump:"
echo "       git add Formula/shellllm.rb && git commit -m 'chore(brew): bump to $TAG'"
echo "  3. mirror Formula/shellllm.rb into the tap repo (homebrew-shellllm):"
echo "       cp Formula/shellllm.rb ../homebrew-shellllm/Formula/shellllm.rb"
echo "       (cd ../homebrew-shellllm && git add Formula/shellllm.rb \\"
echo "          && git commit -m '$TAG' && git push)"
echo "  4. verify the user-facing install path:"
echo "       brew update && brew reinstall francoischastel/shellllm/shellllm"
