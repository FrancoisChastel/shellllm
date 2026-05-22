"""Tests for the filesystem hard wall. Failure here means an LLM can read
files it shouldn't, so this suite is the gate before anything else ships."""

from __future__ import annotations

import os

import pytest

from shellllm.safe_fs import (
    DENYLIST_HOME,
    MAX_READ_BYTES,
    WallViolation,
    safe_read,
    safe_read_text,
    safe_resolve,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """$HOME and $PWD both inside tmp_path. PWD is a subdir of HOME."""
    home_dir = (tmp_path / "home").resolve()
    home_dir.mkdir()
    work_dir = home_dir / "work"
    work_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(work_dir)
    return home_dir


@pytest.fixture
def split_roots(tmp_path, monkeypatch):
    """$PWD lives outside $HOME — exercises the second allowed root."""
    home_dir = (tmp_path / "home").resolve()
    home_dir.mkdir()
    work_dir = (tmp_path / "elsewhere" / "work").resolve()
    work_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(work_dir)
    return home_dir, work_dir


# ─── Containment ────────────────────────────────────────────────────────


def test_file_inside_home_allowed(home):
    f = home / "notes.txt"
    f.write_text("hello")
    assert safe_resolve(f) == f.resolve()


def test_file_inside_pwd_outside_home_allowed(split_roots):
    _, work = split_roots
    f = work / "local.txt"
    f.write_text("hi")
    assert safe_resolve(f) == f.resolve()


def test_etc_passwd_denied(home):
    with pytest.raises(WallViolation):
        safe_resolve("/etc/passwd")


def test_parent_traversal_denied(home):
    with pytest.raises(WallViolation):
        safe_resolve("../" * 10 + "etc/passwd")


def test_absolute_outside_denied(home):
    with pytest.raises(WallViolation):
        safe_resolve("/tmp")


def test_sibling_path_denied(split_roots):
    home_dir, work = split_roots
    # A path that's inside tmp_path but neither in $HOME nor $PWD.
    sibling = work.parent.parent / "stranger.txt"
    sibling.write_text("nope")
    with pytest.raises(WallViolation):
        safe_resolve(sibling)


# ─── Symlinks ───────────────────────────────────────────────────────────


def test_symlink_inside_home_allowed(home):
    target = home / "target.txt"
    target.write_text("ok")
    link = home / "link.txt"
    link.symlink_to(target)
    assert safe_resolve(link) == target.resolve()


def test_symlink_escaping_home_denied(home, tmp_path):
    outside = (tmp_path / "outside.txt").resolve()
    outside.write_text("nope")
    link = home / "escape.txt"
    link.symlink_to(outside)
    with pytest.raises(WallViolation):
        safe_resolve(link)


def test_symlink_to_etc_passwd_denied(home):
    link = home / "passwd.txt"
    link.symlink_to("/etc/passwd")
    with pytest.raises(WallViolation):
        safe_resolve(link)


def test_symlink_through_denylist_denied(home):
    # A link in HOME root pointing into ~/.ssh must be denied.
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY")
    link = home / "totally_innocent.txt"
    link.symlink_to(home / ".ssh" / "id_rsa")
    with pytest.raises(WallViolation):
        safe_resolve(link)


# ─── File types ─────────────────────────────────────────────────────────


def test_directory_denied(home):
    with pytest.raises(WallViolation):
        safe_resolve(home)


def test_missing_file_denied(home):
    with pytest.raises(WallViolation):
        safe_resolve(home / "does-not-exist.txt")


def test_fifo_denied(home):
    fifo = home / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(WallViolation):
        safe_resolve(fifo)


def test_dev_null_denied(home):
    # /dev/null lives outside the wall, so containment catches it first.
    with pytest.raises(WallViolation):
        safe_resolve("/dev/null")


# ─── Inside-HOME denylist ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "relative",
    [
        ".ssh/id_rsa",
        ".ssh/config",
        ".aws/credentials",
        ".gnupg/private-keys-v1.d/foo.key",
        ".config/gh/hosts.yml",
        ".docker/config.json",
        ".kube/config",
        ".netrc",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        "Library/Keychains/login.keychain-db",
    ],
)
def test_denylisted_paths_inside_home(home, relative):
    f = home / relative
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("secret")
    with pytest.raises(WallViolation):
        safe_resolve(f)


def test_lookalike_ssh_directory_allowed(home):
    # ".sshfoo" must not match the ".ssh" denylist entry.
    d = home / ".sshfoo"
    d.mkdir()
    f = d / "file.txt"
    f.write_text("ok")
    assert safe_resolve(f) == f.resolve()


def test_lookalike_ssh_filename_allowed(home):
    # "ssh-notes.txt" is a normal file in $HOME root.
    f = home / "ssh-notes.txt"
    f.write_text("hi")
    assert safe_resolve(f) == f.resolve()


def test_denylist_entries_cover_known_secrets():
    # If someone removes an entry by accident, this test catches it.
    must_have = {".ssh", ".aws", ".gnupg", ".netrc", ".git-credentials"}
    assert must_have <= DENYLIST_HOME


# ─── Expansion ──────────────────────────────────────────────────────────


def test_tilde_expansion(home):
    f = home / "tilde.txt"
    f.write_text("hi")
    assert safe_resolve("~/tilde.txt") == f.resolve()


def test_env_var_expansion(home, monkeypatch):
    f = home / "envvar.txt"
    f.write_text("hi")
    monkeypatch.setenv("MYDIR", str(home))
    assert safe_resolve("$MYDIR/envvar.txt") == f.resolve()


# ─── Reading ────────────────────────────────────────────────────────────


def test_safe_read_returns_bytes(home):
    f = home / "data.bin"
    f.write_bytes(b"\x00\x01\x02")
    assert safe_read(f) == b"\x00\x01\x02"


def test_safe_read_caps_at_max_bytes(home):
    f = home / "big.bin"
    f.write_bytes(b"a" * (MAX_READ_BYTES + 1024))
    assert len(safe_read(f)) == MAX_READ_BYTES


def test_safe_read_text_decodes_utf8(home):
    f = home / "text.txt"
    f.write_text("héllo")
    assert safe_read_text(f) == "héllo"


def test_safe_read_text_replaces_bad_utf8(home):
    f = home / "bad.bin"
    f.write_bytes(b"\xff\xfe\xfd")
    assert isinstance(safe_read_text(f), str)


def test_safe_read_denies_outside(home):
    with pytest.raises(WallViolation):
        safe_read("/etc/passwd")


def test_safe_read_denies_denylisted(home):
    (home / ".ssh").mkdir()
    f = home / ".ssh" / "id_rsa"
    f.write_text("PRIVATE")
    with pytest.raises(WallViolation):
        safe_read(f)
