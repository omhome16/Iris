"""Sandbox tests — the jailed file system Iris may touch.

The core guarantee: nothing outside the sandbox root is reachable, no matter
how the path is phrased (.., absolute, drive letter).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.sandbox import Sandbox, SandboxError


@pytest.fixture
def box(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path / "sandbox")


def test_create_read_write_list_roundtrip(box: Sandbox):
    box.create("notes/idea.md", "# Idea\n\nBuild a thing.")
    assert box.read("notes/idea.md") == "# Idea\n\nBuild a thing."

    box.write("notes/idea.md", "# Idea v2")
    assert box.read("notes/idea.md") == "# Idea v2"

    entries = box.list("notes")
    assert entries == [{"path": "notes/idea.md", "bytes": len("# Idea v2")}]


def test_create_refuses_existing(box: Sandbox):
    box.create("a.txt", "x")
    with pytest.raises(SandboxError, match="already exists"):
        box.create("a.txt", "y")


def test_traversal_is_rejected(box: Sandbox, tmp_path: Path):
    # a hostile sibling next to the sandbox — the prize the traversal wants
    victim = tmp_path / "victim.txt"
    victim.write_text("top secret", encoding="utf-8")

    for evil in ("../victim.txt", "..\\victim.txt", "sub/../../victim.txt", "C:\\windows\\x"):
        with pytest.raises(SandboxError):
            box.read(evil)
        with pytest.raises(SandboxError):
            box.write(evil, "pwned")

    assert victim.read_text(encoding="utf-8") == "top secret"


def test_absolute_path_rejected(box: Sandbox, tmp_path: Path):
    # On POSIX this is rejected as absolute; on Windows it first trips the
    # backslash check — the guarantee is the same either way.
    with pytest.raises(SandboxError, match=r"(absolute|backslashes|drive)"):
        box.read(str(tmp_path / "sandbox" / "whatever.md"))


def test_missing_file_is_an_error(box: Sandbox):
    with pytest.raises(SandboxError, match="not a file"):
        box.read("does-not-exist.md")


def test_overwrite_is_implicit_in_write(box: Sandbox):
    box.write("log.md", "first")
    box.write("log.md", "second")
    assert box.read("log.md") == "second"
