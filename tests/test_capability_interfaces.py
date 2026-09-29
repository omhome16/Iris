"""Phase 1 conformance tests: the core implementations honor their interfaces,
the registries expose them by the configured names, and an unknown backend name
fails fast instead of at the first call.

These are the seams every future backend plugs into, so their contracts are
pinned here rather than discovered when a swap silently half-works."""

from __future__ import annotations

import pytest

from iris_ai.capabilities.judges import JUDGES, Judge
from iris_ai.capabilities.judges import REQUIRED as JUDGE_REQUIRED
from iris_ai.capabilities.memory import (
    MEMORY_BACKENDS,
    MemoryBackend,
)
from iris_ai.capabilities.memory import (
    REQUIRED as MEMORY_REQUIRED,
)
from iris_ai.capabilities.models import MODELS, ModelBackend
from iris_ai.capabilities.models import REQUIRED as MODEL_REQUIRED
from iris_ai.config import settings
from iris_ai.jev.client import JevClient
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.llm import LLMClient
from iris_ai.memory.null_index import NullIndex
from iris_ai.memory.sqlite_index import SqliteIndex
from iris_ai.registry import RegistryError


def _has(cls, names):
    return {name: hasattr(cls, name) for name in names}


# ── the core class satisfies the interface it is registered as ─────────────
@pytest.mark.parametrize("cls, required", [
    (LLMClient, MODEL_REQUIRED),
    (MemoryIndex, MEMORY_REQUIRED),
    (SqliteIndex, MEMORY_REQUIRED),
    (NullIndex, MEMORY_REQUIRED),
    (JevClient, JUDGE_REQUIRED),
])
def test_core_implementations_declare_the_protocol_methods(cls, required):
    missing = [name for name, present in _has(cls, required).items() if not present]
    assert not missing, f"{cls.__name__} is missing {missing}"


def test_runtime_checkable_protocols_accept_the_core_instances():
    # A bare instance (no __init__) is enough: runtime_checkable checks the shape.
    assert isinstance(object.__new__(LLMClient), ModelBackend)
    assert isinstance(object.__new__(NullIndex), MemoryBackend)
    assert isinstance(object.__new__(JevClient), Judge)


# ── the registry exposes the core implementations ─────────────────────────
def test_registries_hold_the_core_names():
    assert "litellm" in MODELS
    assert {"pgvector", "sqlite", "null"} <= set(MEMORY_BACKENDS.names())
    assert "jev" in JUDGES


def test_the_null_backend_builds_a_conforming_object():
    backend = MEMORY_BACKENDS.build("null", dsn="postgresql://x", llm=None, reranker=None)
    assert isinstance(backend, NullIndex)
    assert all(hasattr(backend, name) for name in MEMORY_REQUIRED)


def test_the_sqlite_backend_builds_from_the_uniform_boot_context(monkeypatch, tmp_path):
    """The engine passes `dsn=…` to every backend; `sqlite` must take its own
    location from settings and ignore the DSN rather than choke on it."""
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "memory.db"))
    backend = MEMORY_BACKENDS.build("sqlite", dsn="postgresql://x", llm=None, reranker=None)
    assert isinstance(backend, SqliteIndex)
    assert backend.db_path == tmp_path / "memory.db"
    assert all(hasattr(backend, name) for name in MEMORY_REQUIRED)


def test_a_configured_backend_name_always_resolves():
    """The defaults in Settings must name a registered backend, or boot breaks
    with a confusing error instead of a clear one."""
    assert settings.model_backend in MODELS
    assert settings.memory_backend in MEMORY_BACKENDS
    assert settings.judge_backend in JUDGES


def test_an_unknown_backend_name_fails_fast_with_the_known_list():
    with pytest.raises(RegistryError, match=r"unknown model_backend 'nope'.*litellm"):
        MODELS.get("nope")


def test_discovery_is_safe_with_no_plugins_installed():
    from iris_ai.capabilities.judges import discover_judges
    from iris_ai.capabilities.memory import discover_memory_backends
    from iris_ai.capabilities.models import discover_models

    assert isinstance(discover_models(), list)
    assert isinstance(discover_memory_backends(), list)
    assert isinstance(discover_judges(), list)
