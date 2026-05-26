# Homebrew distribution

Two audiences live in this doc: **users** who want `brew install`, and the **maintainer** who has to keep the formula honest.

## For users

```sh
brew tap FrancoisChastel/shellllm
brew install shellllm
```

or the one-shot form:

```sh
brew install FrancoisChastel/shellllm/shellllm
```

Both install:

- `shellllm-comma` and `shellllm-ask` on `$PATH`
- `llama.cpp` and `fzf` as hard dependencies
- the zsh integration file at `$(brew --prefix)/share/shellllm/shellllm.zsh`

Finish wiring it into zsh:

```sh
echo 'source "$(brew --prefix)/share/shellllm/shellllm.zsh"' >> ~/.zshrc
exec zsh
??              # start the backend
?? --list       # see which tiers are downloaded
```

Tier model downloads still need `huggingface-cli`:

```sh
pipx install huggingface_hub
```

## For the maintainer

### Topology

There are two repos:

| Repo | Role |
| --- | --- |
| `FrancoisChastel/shellllm` (this one) | source of truth — code, tests, **and** the canonical `Formula/shellllm.rb` |
| `FrancoisChastel/homebrew-shellllm` | the Homebrew tap users actually tap. Contains a single `Formula/shellllm.rb`, mirrored from this repo on every release. |

Homebrew taps **must** be named `homebrew-<name>` — there's no way around the two-repo split if you want `brew tap FrancoisChastel/shellllm` to work.

### One-time bootstrap of the tap repo

```sh
# from a clean directory next to this repo
brew tap-new FrancoisChastel/shellllm     # creates the directory + scaffold
cp Formula/shellllm.rb ../homebrew-shellllm/Formula/shellllm.rb
cd ../homebrew-shellllm
git add . && git commit -m "initial: shellllm formula"
gh repo create FrancoisChastel/homebrew-shellllm --public --source=. --push
```

### Release flow (per version)

1. Bump `version` in `pyproject.toml` **and** `src/shellllm/__init__.py`.
2. Commit the bump on `main`.
3. From the repo root:
   ```sh
   scripts/brew/release.sh
   ```
   This tags, pushes, downloads the GitHub tarball, computes the SHA-256, and rewrites the formula's `url` + main `sha256`.
4. If you changed any runtime dep in `pyproject.toml` since the last release, refresh the resource stanzas:
   ```sh
   scripts/brew/generate-resources.sh --in-place
   ```
5. Commit the formula bump on `main`.
6. Mirror the formula into the tap repo and push:
   ```sh
   cp Formula/shellllm.rb ../homebrew-shellllm/Formula/shellllm.rb
   (cd ../homebrew-shellllm && git add Formula/shellllm.rb \
      && git commit -m "v$(python3 -c 'import tomllib,pathlib; \
         print(tomllib.loads(pathlib.Path(\"pyproject.toml\").read_text())[\"project\"][\"version\"])')" \
      && git push)
   ```
7. Verify end-to-end:
   ```sh
   brew update
   brew uninstall shellllm 2>/dev/null || true
   brew install FrancoisChastel/shellllm/shellllm
   brew test shellllm
   ```

### Why the formula isn't in homebrew-core

`homebrew-core` requires notability (GitHub stars, package-manager presence) and a sustained maintenance commitment. A personal tap is the right home until shellllm is well past that bar.

### Sentinel values in the formula

The committed `Formula/shellllm.rb` ships with `REPLACE_AT_RELEASE_TARBALL_SHA256` for the main tarball and `REPLACE_RESOURCE_SHA256` for every PyPI resource. The two scripts above replace them; `brew audit --new-formula Formula/shellllm.rb` will fail loudly if any sentinel survives.

### Local smoke test before pushing the tap

```sh
brew install --build-from-source --verbose Formula/shellllm.rb
brew test --verbose shellllm
brew audit --strict --online Formula/shellllm.rb
```
