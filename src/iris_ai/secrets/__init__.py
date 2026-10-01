"""Secret storage — one interface, three backends, and one rule about values.

`05-safety.md` §6 asks for tokens to live in the OS keychain rather than in the
manifest, the journal, or a committable file. That is a *storage* decision, and
this module is the storage. What matters as much as where a secret lives is that
nothing else in Iris has to know: every caller asks for a **name**, and a value is
never logged, echoed, or returned to anything that prints.

Three backends, in the order an operator would want them:

- **`env`** — the process environment. Always consulted first, because an
  environment variable is the most explicit thing an operator can set (and what a
  container injects). Read-only through this interface: writing into the process
  environment from inside the app is how a secret ends up inherited by every child
  process the harness spawns, including a skill's script.
- **`keyring`** — the OS keychain (macOS Keychain, Windows Credential Manager,
  freedesktop Secret Service), encrypted at rest and unlocked by the login
  session. Optional: it needs the `keyring` extra
  (`pip install 'iris-personal-ai[secrets]'`), and Iris refuses it with that
  sentence rather than falling back silently.
- **`file`** — a JSON file under `config/`, chmod 0600. Honest about what it is:
  **not encrypted**, readable by your OS user (and by anything running as that
  user). It exists because a fresh clone must work with no extra dependency, and
  a secret in a 0600 file is still better than a secret in `.env` that a blanket
  `git add` can publish. `location()` says so in exactly those words.

`auto` (the default) picks `keyring` when it is installed and `file` otherwise, and
reports which one it chose — the choice is visible in `iris doctor` and
`iris secrets`, because "where is my token" should never be a guess.

Provenance note: names are not secrets, values are. Everything that lists (the
CLI, the doctor, the traces) lists names.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from iris_ai.config import settings
from iris_ai.registry import Registry

log = logging.getLogger("iris.secrets")

#: The keychain namespace Iris stores under, so its entries are identifiable and
#: removable without touching another application's.
KEYRING_SERVICE = "iris"

BACKENDS: tuple[str, ...] = ("auto", "env", "keyring", "file")


class SecretStoreError(RuntimeError):
    """The store cannot do what was asked, with the reason said out loud."""


@runtime_checkable
class SecretStore(Protocol):
    """Get, set and forget one named secret. Never a value in a message."""

    name: str

    def get(self, name: str) -> str | None:  # pragma: no cover - protocol
        ...

    def set(self, name: str, value: str) -> None:  # pragma: no cover - protocol
        ...

    def delete(self, name: str) -> bool:  # pragma: no cover - protocol
        ...

    def location(self) -> str:  # pragma: no cover - protocol
        ...


#: Registered stores. A plugin that registers one makes it selectable by
#: `SECRET_STORE`, the same way every other capability in Iris works.
SECRET_STORES: Registry[SecretStore] = Registry("secret_store")


@dataclass(frozen=True, slots=True)
class EnvStore:
    """The process environment, read-only. See the module docstring for why."""

    name: str = "env"

    def get(self, name: str) -> str | None:
        return os.environ.get(name) or None

    def set(self, name: str, value: str) -> None:
        raise SecretStoreError(
            f"refusing to set {name} in the process environment: it would be inherited "
            "by every child process Iris spawns, a skill's script included. Put it in "
            ".env (which is read at boot) or in the keychain/file store."
        )

    def delete(self, name: str) -> bool:
        return False

    def location(self) -> str:
        return "the process environment (and .env, which is loaded into it) — read-only here"


@dataclass(frozen=True, slots=True)
class KeyringStore:
    """The OS keychain. Optional, and loud when it is missing."""

    name: str = "keyring"

    @staticmethod
    def available() -> bool:
        try:
            import keyring  # noqa: F401
        except Exception:  # noqa: BLE001 - any import failure means "not installed"
            return False
        return True

    @staticmethod
    def _backend():
        if not KeyringStore.available():
            raise SecretStoreError(
                "the keyring backend needs the optional extra: "
                "`pip install 'iris-personal-ai[secrets]'` (or set SECRET_STORE=file "
                "to keep secrets in a 0600 file instead)"
            )
        try:
            import keyring
        except Exception as exc:
            raise SecretStoreError(
                "the keyring backend needs the optional extra: "
                "`pip install 'iris-personal-ai[secrets]'` (or set SECRET_STORE=file "
                "to keep secrets in a 0600 file instead)"
            ) from exc
        return keyring

    def get(self, name: str) -> str | None:
        return self._backend().get_password(KEYRING_SERVICE, name) or None

    def set(self, name: str, value: str) -> None:
        self._backend().set_password(KEYRING_SERVICE, name, value)

    def delete(self, name: str) -> bool:
        keyring = self._backend()
        if keyring.get_password(KEYRING_SERVICE, name) is None:
            return False
        keyring.delete_password(KEYRING_SERVICE, name)
        return True

    def location(self) -> str:
        if not self.available():
            return "the OS keychain (unavailable: the `keyring` extra is not installed)"
        return f"the OS keychain (service {KEYRING_SERVICE!r})"


@dataclass(frozen=True, slots=True)
class FileStore:
    """A JSON file only this OS user can read. Not encrypted — and it says so."""

    path: Path
    name: str = "file"

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SecretStoreError(f"{self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SecretStoreError(f"{self.path}: expected an object of name -> value")
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _restrict(self.path)

    def get(self, name: str) -> str | None:
        return self._load().get(name) or None

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True

    def location(self) -> str:
        return (
            f"the file {self.path} (readable only by your OS user; NOT encrypted — "
            "install the keyring extra for OS-level encryption)"
        )


#: The built-ins, registered like every other capability so a plugin can add a
#: store (Vault, 1Password, an HSM) and have it selectable by name — no core edit.
#: The factories swallow extra keyword arguments because `resolve_store` passes the
#: file path to all of them, and a store with no file should not have to care.
SECRET_STORES.register("env", lambda **_: EnvStore(), source="core")
SECRET_STORES.register("keyring", lambda **_: KeyringStore(), source="core")
SECRET_STORES.register("file", lambda path=None, **_: FileStore(path or secrets_path()), source="core")


def discover_secret_stores() -> list[str]:
    """Register installed secret-store plugins; returns the names that were added."""
    return [reg.name for reg in SECRET_STORES.discover("iris_ai.secret_stores")]


def _restrict(path: Path) -> None:
    """Best-effort 0600. Windows has no mode bits; the file is user-scoped there.

    `os.chmod` on Windows only toggles the read-only flag, so claiming a mode
    there would be a false statement about the file's protection. The store says
    what it is instead.
    """
    if os.name == "nt":
        return
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:  # pragma: no cover - platform/permission specific
        log.warning("could not restrict permissions on %s: %s", path, exc)


def secrets_path() -> Path:
    """Where the file backend lives: under the workspace, beside the other state."""
    return Path(settings.secrets_file)


def available_backends() -> dict[str, bool]:
    """Which backends exist on this machine, for `iris secrets` and `iris doctor`."""
    return {"env": True, "keyring": KeyringStore.available(), "file": True}


def resolve_store(kind: str | None = None) -> SecretStore:
    """The configured store, or the one `auto` picks. Never silently degrades.

    `auto` *chooses* (keyring when installed, else the file) rather than falling
    back at failure time: a store that worked yesterday and quietly became another
    one today is how an operator stops knowing where their tokens are.
    """
    kind = (kind or settings.secret_store or "auto").strip().lower()
    if kind not in BACKENDS:
        raise SecretStoreError(f"unknown SECRET_STORE {kind!r}; expected one of {', '.join(BACKENDS)}")
    discover_secret_stores()
    if kind == "auto":
        kind = "keyring" if KeyringStore.available() else "file"
    return SECRET_STORES.build(kind, path=secrets_path())


def lookup(name: str) -> tuple[str | None, str]:
    """Find one secret, and say **where** it came from.

    The second element is the point: `iris secrets list` can report "missing"
    versus "found in the environment" versus "found in the keychain", which is the
    difference between a diagnostic and a shrug. The value is never logged.
    """
    found = os.environ.get(name)
    if found:
        return found, "env"
    store = resolve_store()
    if store.name == "env":
        return None, "missing"
    value = store.get(name)
    if value:
        return value, store.name
    return None, "missing"


__all__ = [
    "BACKENDS",
    "KEYRING_SERVICE",
    "SECRET_STORES",
    "EnvStore",
    "FileStore",
    "KeyringStore",
    "SecretStore",
    "SecretStoreError",
    "available_backends",
    "discover_secret_stores",
    "lookup",
    "resolve_store",
    "secrets_path",
]
