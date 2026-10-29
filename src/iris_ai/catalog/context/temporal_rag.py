"""Newest-first recall. The folder under `temporal-rag/` is the implementation.

The subprocess host loads that folder and cannot import the kernel. This module
re-exports the same class so the in-process builtin and the eval scorer stay
on one body.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parent / "temporal-rag" / "component.py"
    spec = importlib.util.spec_from_file_location("iris_catalog_temporal_rag_component", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_module = _load()
layout = _module.layout
TemporalRag = _module.TemporalRag
