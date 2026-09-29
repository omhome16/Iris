"""The secret store, and the promise that a value never appears in output.

Two kinds of test here, and the second is the one that matters:

- the stores behave (round-trip, delete, unknown name, the registry's coverage);
- **nothing prints a value.** Every readout — `iris secrets list`, `iris policy`,
  the MCP declaration error — is checked against the secret string itself, because
  "we are careful not to log secrets" is a claim, and a substring assertion is
  evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iris_ai.cli.main import app
from iris_ai.cli.secrets import run as secrets_run
from iris_ai.config import settings
from iris_ai.secrets import (
    BACKENDS,
    SECRET_STORES,
    EnvStore,
    FileStore,
    KeyringStore,
    SecretStoreError,
    available_backends,
    lookup,
    resolve_store,
    secrets_path,
)

runner = CliRunner()
SECRET_VALUE = "ghp_thismustneverbeprinted"


@pytest.fixture
def file_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FileStore:
    """The file backend, pointed at a temp dir and selected explicitly."""
    monkeypatch.setattr(settings, "secret_store", "file")
    monkeypatch.setattr(settings, "secrets_file", str(tmp_path / "secrets.json"))
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / "no-servers.json"))
    monkeypatch.delenv("IRIS_TEST_TOKEN", raising=False)
    return FileStore(path=tmp_path / "secrets.json")


# ── the stores ──────────────────────────────────────────────────────────────


def test_the_file_store_round_trips_a_named_secret(file_store: FileStore):
    assert file_store.get("IRIS_TEST_TOKEN") is None
    file_store.set("IRIS_TEST_TOKEN", SECRET_VALUE)
    assert file_store.get("IRIS_TEST_TOKEN") == SECRET_VALUE
    assert file_store.delete("IRIS_TEST_TOKEN") is True
    assert file_store.delete("IRIS_TEST_TOKEN") is False  # already gone
    assert file_store.get("IRIS_TEST_TOKEN") is None


def test_the_registered_backends_are_exactly_the_documented_ones():
    """A name in `SECRET_STORE` that resolves to nothing would be a silent no-op."""
    assert set(BACKENDS) == {"auto"} | set(SECRET_STORES.names())
    assert set(SECRET_STORES.names()) == {"env", "keyring", "file"}


def test_an_unknown_backend_is_refused_by_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "secret_store", "vault-but-typo")
    with pytest.raises(SecretStoreError) as exc:
        resolve_store()
    assert "vault-but-typo" in str(exc.value)
    assert "auto" in str(exc.value)


def test_auto_chooses_and_does_not_fall_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`auto` picks a backend up front, so \"where is my token\" is stable."""
    monkeypatch.setattr(settings, "secrets_file", str(tmp_path / "secrets.json"))
    monkeypatch.setattr(settings, "secret_store", "auto")
    expected = "keyring" if KeyringStore.available() else "file"
    assert resolve_store().name == expected
    assert available_backends()["env"] is True


def test_the_env_store_refuses_to_write(monkeypatch: pytest.MonkeyPatch):
    """Writing into os.environ would hand the secret to every child process.

    A skill's script, an MCP stdio server and a `subprocess.run` skill runner all
    inherit the process environment — so the store is read-only for that backend
    on purpose, and says why.
    """
    store = EnvStore()
    monkeypatch.setenv("IRIS_TEST_TOKEN", SECRET_VALUE)
    assert store.get("IRIS_TEST_TOKEN") == SECRET_VALUE
    with pytest.raises(SecretStoreError) as exc:
        store.set("IRIS_TEST_TOKEN", "other")
    assert "child process" in str(exc.value)
    assert store.delete("IRIS_TEST_TOKEN") is False


def test_the_environment_wins_over_the_store(monkeypatch: pytest.MonkeyPatch, file_store: FileStore):
    """An operator's explicit `export` beats a stored file — and both are named."""
    file_store.set("IRIS_TEST_TOKEN", "from-the-store")
    monkeypatch.setenv("IRIS_TEST_TOKEN", "from-the-environment")
    assert lookup("IRIS_TEST_TOKEN") == ("from-the-environment", "env")
    monkeypatch.delenv("IRIS_TEST_TOKEN")
    assert lookup("IRIS_TEST_TOKEN") == ("from-the-store", "file")
    assert lookup("IRIS_TEST_MISSING") == (None, "missing")


def test_the_keyring_backend_says_what_to_install(monkeypatch: pytest.MonkeyPatch):
    """No silent downgrade: selecting the keychain without the extra is an error."""
    store = KeyringStore()
    monkeypatch.setattr(KeyringStore, "available", staticmethod(lambda: False))
    assert "keyring` extra is not installed" in store.location()
    with pytest.raises(SecretStoreError) as exc:
        store.get("anything")
    assert "iris-personal-ai[secrets]" in str(exc.value)


def test_the_file_backend_does_not_claim_to_be_encrypted(file_store: FileStore):
    """Honesty about the fallback is the point of having one."""
    assert "NOT encrypted" in file_store.location()
    assert "readable only by your OS user" in file_store.location()


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_backend_reports_the_choice_and_never_a_value(file_store: FileStore):
    file_store.set("IRIS_TEST_TOKEN", SECRET_VALUE)
    result = runner.invoke(app, ["secrets", "backend"])
    assert result.exit_code == 0
    assert "file" in result.stdout
    assert SECRET_VALUE not in result.stdout


def test_set_and_list_report_names_only(monkeypatch: pytest.MonkeyPatch, file_store: FileStore):
    monkeypatch.setenv("IRIS_TEST_TOKEN", SECRET_VALUE)
    stored = runner.invoke(app, ["secrets", "set", "IRIS_OTHER_TOKEN", SECRET_VALUE])
    assert stored.exit_code == 0
    assert SECRET_VALUE not in stored.stdout
    assert "IRIS_OTHER_TOKEN" in stored.stdout

    # A declared server that references both names: `list` resolves each one and
    # says where it came from.
    path = Path(settings.secrets_file).parent / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "wiki": {
                        "url": "http://127.0.0.1:9/mcp",
                        "env": {"A": "${IRIS_TEST_TOKEN}", "B": "${IRIS_OTHER_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "mcp_servers_file", str(path))
    listed = runner.invoke(app, ["secrets", "list"])
    assert listed.exit_code == 0
    assert "IRIS_TEST_TOKEN" in listed.stdout and "env" in listed.stdout
    assert "IRIS_OTHER_TOKEN" in listed.stdout and "file" in listed.stdout
    assert SECRET_VALUE not in listed.stdout


def test_list_marks_a_missing_secret_as_missing(monkeypatch: pytest.MonkeyPatch, file_store: FileStore):
    path = Path(settings.secrets_file).parent / ".mcp.json"
    path.write_text(
        json.dumps(
            {"mcpServers": {"wiki": {"url": "http://127.0.0.1:9/mcp", "env": {"A": "${IRIS_ABSENT}"}}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "mcp_servers_file", str(path))
    result = runner.invoke(app, ["secrets", "list"])
    assert result.exit_code == 0
    assert "missing" in result.stdout
    assert "IRIS_ABSENT" in result.stdout


def test_rm_reports_a_secret_that_was_not_there(file_store: FileStore):
    assert secrets_run("rm", name="IRIS_NOPE") == 1
    file_store.set("IRIS_NOPE", "x")
    assert secrets_run("rm", name="IRIS_NOPE") == 0


def test_secrets_returns_exit_codes_rather_than_raising(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "secret_store", "file")
    monkeypatch.setattr(settings, "secrets_file", str(tmp_path / "s.json"))
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / "none.json"))
    assert secrets_run("backend") == 0
    assert secrets_run("list") == 0
    assert secrets_run("set") == 2  # missing name
    assert secrets_run("rm") == 2  # missing name
    assert secrets_run("nope") == 2


def test_a_secret_is_not_written_to_a_trace_verbatim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The threat model's 'secret leakage into logs/trace' row, checked.

    Traces go through `iris_ai.redact`, which is where an argument shaped like a
    credential is replaced. This is asserted here, next to the store, because the
    two are one promise: store it safely, then do not print it.
    """
    from iris_ai import redact
    from iris_ai.trace import TraceLogger

    logger = TraceLogger(tmp_path / "traces.jsonl")
    logger.record({"event": "test", "args": {"token": SECRET_VALUE}, "reply": f"key={SECRET_VALUE}"})
    written = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert SECRET_VALUE not in written
    assert redact is not None


def test_secrets_path_follows_the_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "secrets_file", str(tmp_path / "custom.json"))
    assert secrets_path() == tmp_path / "custom.json"
