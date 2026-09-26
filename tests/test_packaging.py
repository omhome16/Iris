"""Packaging and sample-config discipline (P8).

`uv build` + installing the wheel is CI's `package` job, because building a wheel
inside the test suite would make every local run pay for it. These tests check the
invariants that job depends on: one version source, a declared console entry, and
a sample config whose keys all name real settings.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import iris_ai
from iris_ai.config import Settings

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
ENV_EXAMPLE = ROOT / ".env.example"

# Environment variables Iris reads directly (not through `Settings`), so their
# presence in the sample config is legitimate. Kept explicit on purpose: the test
# is only useful if the allowlist cannot hide an undocumented knob silently.
DIRECT_ENV = {
    "IRIS_DEBUG",  # CLI flag passthrough
    "IRIS_TEST_POSTGRES_DSN",  # the test suite's separate database
    "IRIS_EVAL_POSTGRES_DSN",  # the eval lab's separate database
}

# Settings that exist for code, not for operators: set by a caller at runtime
# rather than typed into `.env`. Kept as an explicit list so "internal" is a
# decision somebody made, the same way tool classes are declared rather than
# implied.
INTERNAL_SETTINGS: set[str] = set()


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_there_is_exactly_one_version_and_hatch_reads_that_one():
    project = _pyproject()["project"]
    assert "version" not in project  # no second copy to drift
    assert project["dynamic"] == ["version"]
    assert _pyproject()["tool"]["hatch"]["version"]["path"] == "src/iris_ai/__init__.py"

    source = (ROOT / "src" / "iris_ai" / "__init__.py").read_text(encoding="utf-8")
    declared = re.search(r'__version__\s*=\s*"([^"]+)"', source).group(1)
    assert declared == iris_ai.__version__


def test_the_console_entry_point_is_declared():
    assert _pyproject()["project"]["scripts"]["iris"] == "iris_ai.cli.main:app"


def test_the_support_doc_exists_and_names_the_python_floor():
    doc = (ROOT / "docs" / "support.md").read_text(encoding="utf-8")
    assert "requires-python" in doc or "Python" in doc
    assert _pyproject()["project"]["requires-python"] == ">=3.12"


def _env_keys() -> list[str]:
    keys: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


def test_every_sample_config_key_names_a_real_setting():
    """A `.env.example` that documents a knob nobody reads is a lie."""
    fields = {name.upper() for name in Settings.model_fields}
    unknown = sorted({key for key in _env_keys() if key.upper() not in fields and key not in DIRECT_ENV})
    assert not unknown, f"sample-config keys with no setting: {unknown}"


def test_the_sample_config_is_not_empty():
    assert len(_env_keys()) > 20  # it is the documented surface, not a stub


def test_every_setting_is_documented_in_the_sample_config():
    """The reverse of the check above.

    That one stops the sample config from documenting a knob nobody reads; this
    one stops a knob existing that the sample config never mentions. Together
    they make `.env.example` the actual surface rather than a best-effort
    sketch: an operator cannot discover a limit that is silently shaping
    behaviour, and the P8 audit found fifty such settings.
    """
    keys = {key.upper() for key in _env_keys()}
    undocumented = sorted(
        name.upper()
        for name in Settings.model_fields
        if name.upper() not in keys and name.upper() not in INTERNAL_SETTINGS
    )
    assert not undocumented, f"settings missing from .env.example: {undocumented}"


def test_env_example_documents_the_p8_knobs():
    keys = {key.upper() for key in _env_keys()}
    for expected in (
        "TOOL_GUARD_ENABLED",
        "BUDGET_MAX_TOKENS_PER_DAY",
        "APPROVAL_BIND_DIGEST",
        "COMPUTER_ENABLED",
        "TOOL_SURFACE_BUDGET",
    ):
        assert expected in keys, expected
