"""Filesystem hard wall.

Every file read by an LLM tool goes through here. The wall has four rules:

1. Canonicalize first. ``.resolve(strict=True)`` flattens ``..`` and chases
   symlinks, so a link in $HOME pointing at /etc/passwd resolves to
   /etc/passwd and gets caught by rule 2.
2. The resolved path must be a descendant of $HOME or $PWD.
3. The resolved path must not fall inside the inside-HOME denylist
   (.ssh, .aws, .gnupg, ...). Path-component match — ``.sshfoo`` is fine.
4. The resolved path must be a regular file. Devices, fifos, sockets,
   directories all refuse.

Reads are capped at MAX_READ_BYTES and use ``O_NOFOLLOW`` on the final
component as a small belt-and-suspenders against a symlink swap between
resolve() and open(). The threat model here is an LLM asking us to read
something it shouldn't, not a concurrent attacker, so this is enough.
"""

from __future__ import annotations

import os
from pathlib import Path

MAX_READ_BYTES = 1_000_000

# Paths relative to $HOME that are never readable, even though they're
# inside the wall. Match is by path components (a/b/c) — substring
# matches like ".sshfoo" don't trigger.
DENYLIST_HOME = frozenset(
    {
        ".ssh",
        ".aws",
        ".gnupg",
        ".gpg",
        ".config/gh",
        ".config/git/credentials",
        ".docker/config.json",
        ".kube",
        ".netrc",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        "Library/Keychains",
        "Library/Application Support/Authy Desktop",
        "Library/Application Support/1Password",
    }
)


class WallViolation(PermissionError):
    """Raised when a path violates the filesystem wall."""


def _roots() -> tuple[Path, ...]:
    # Re-read each time so tests that chdir or override $HOME see the change.
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    return (home, cwd)


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _denylisted(real: Path) -> bool:
    home = Path.home().resolve()
    try:
        rel_parts = real.relative_to(home).parts
    except ValueError:
        return False  # not in $HOME, denylist doesn't apply
    for entry in DENYLIST_HOME:
        entry_parts = Path(entry).parts
        if rel_parts[: len(entry_parts)] == entry_parts:
            return True
    return False


def safe_resolve(path: str | os.PathLike[str]) -> Path:
    """Resolve ``path`` and confirm it satisfies the wall. Return the real path."""
    raw = os.fspath(path)
    expanded = Path(os.path.expandvars(raw)).expanduser()
    try:
        real = expanded.resolve(strict=True)
    except FileNotFoundError as exc:
        raise WallViolation(f"path does not exist: {expanded}") from exc

    if not any(_within(real, r) for r in _roots()):
        raise WallViolation(f"path outside $HOME and $PWD: {real}")
    if _denylisted(real):
        raise WallViolation(f"path is in the inside-HOME denylist: {real}")
    if not real.is_file():
        raise WallViolation(f"not a regular file: {real}")
    return real


def safe_read(path: str | os.PathLike[str]) -> bytes:
    """Return the first MAX_READ_BYTES of ``path``."""
    real = safe_resolve(path)
    fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return os.read(fd, MAX_READ_BYTES)
    finally:
        os.close(fd)


def safe_read_text(path: str | os.PathLike[str], encoding: str = "utf-8") -> str:
    return safe_read(path).decode(encoding, errors="replace")
