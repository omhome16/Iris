"""The driver boundary — a missing driver is a refusal, not an exception.

Screen control needs something outside the standard library: a browser driver, a
desktop automation library, or a remote grid. Iris must not acquire a hard
dependency on any of them, and must not find out at call time that one is
missing. So every driver answers two questions up front:

1. **Are you usable?** `availability()` returns `(ok, reason)`. The reason is
   stable and actionable ("install the optional extra"), because it is what the
   owner will read in the refusal.
2. **Do one action.** `perform()` returns an `Observation`, and is never allowed
   to raise into the graph — a driver crash is *data* about the action, not a
   broken turn.

`NullProvider` is the honest default: `computer_provider=null` means "there is no
driver", and it says so rather than pretending the capability exists. The
`PlaywrightProvider` imports lazily inside `availability()` so importing
`iris_ai.computer` never costs a browser stack that most installs do not have.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from iris_ai.computer.actions import Action, ActionKind, Observation

log = logging.getLogger("iris")

# The exact string a user sees when the optional extra is absent. Stable because
# a refusal that changes wording is a refusal nobody can search for.
PLAYWRIGHT_MISSING = (
    "playwright is not installed — computer-use needs the optional extra "
    "(`uv pip install 'iris[computer]'` then `playwright install chromium`)"
)


@runtime_checkable
class ComputerProvider(Protocol):
    """What a driver must be. Structural typing, so a test fake is a provider."""

    name: str

    def availability(self) -> tuple[bool, str]:
        """`(True, "")` when usable, else `(False, stable_reason)`."""
        ...

    async def perform(self, action: Action) -> Observation:  # pragma: no cover - drivers vary
        """Do one action. Return an `Observation`; never raise into the graph."""
        ...


class NullProvider:
    """The no-driver provider. Usable? No — and it says exactly why.

    This is not a no-op that pretends success. A capability nobody granted must
    fail closed, and the refusal must be indistinguishable from "computer-use is
    off", because that is what it is.
    """

    name = "null"

    def __init__(self, reason: str = "computer-use has no driver configured (computer_provider=null)") -> None:
        self._reason = reason

    def availability(self) -> tuple[bool, str]:
        return False, self._reason

    async def perform(self, action: Action) -> Observation:
        _ok, reason = self.availability()
        return Observation(kind=action.kind, ok=False, error=reason, unavailable=True)


class PlaywrightProvider:
    """Browser automation over Playwright, imported only when it is needed.

    Each action opens a short-lived browser context. That is slower than holding
    one open, and it is the right trade for P7: a driver that keeps a browser
    alive across turns keeps *cookies and sessions* alive across turns, which is
    exactly the state a permission model would then have to reason about. The
    per-action lifecycle makes "the grant expired" mean the session is gone too.

    Nothing here raises: a missing package, a navigation timeout and a missing
    selector are all reports about the action.
    """

    name = "playwright"

    def __init__(self, *, timeout_seconds: float = 15.0, headless: bool = True) -> None:
        self._timeout_ms = max(1.0, float(timeout_seconds)) * 1000.0
        self._headless = bool(headless)
        # Cached once: probing is an import, and the answer cannot change while
        # the process runs without a reinstall.
        self._missing: str | None = None

    def availability(self) -> tuple[bool, str]:
        if self._missing is not None:
            return False, self._missing
        try:
            import playwright.async_api  # noqa: F401  # presence probe
        except Exception:  # noqa: BLE001 - any import failure is "not installed"
            self._missing = PLAYWRIGHT_MISSING
            return False, self._missing
        return True, ""

    async def perform(self, action: Action) -> Observation:
        ok, reason = self.availability()
        if not ok:
            return Observation(kind=action.kind, ok=False, error=reason, unavailable=True)
        try:
            return await self._perform(action)
        except Exception as exc:  # noqa: BLE001 - driver failures are data, never a broken turn
            log.debug("playwright action failed: %s", exc)
            return Observation(kind=action.kind, ok=False, error=f"playwright driver failed: {exc}")

    async def _perform(self, action: Action) -> Observation:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await browser.new_page()
                if action.kind is ActionKind.NAVIGATE:
                    response = await page.goto(action.target, timeout=self._timeout_ms, wait_until="domcontentloaded")
                    status = response.status if response is not None else 0
                    return Observation(
                        kind=action.kind,
                        ok=True,
                        detail=f"navigated ({status})",
                        url=page.url,
                        title=await page.title(),
                    )
                if action.kind is ActionKind.SCREENSHOT:
                    data = await page.screenshot()
                    return Observation(
                        kind=action.kind,
                        ok=True,
                        detail=f"screenshot {len(data)} bytes",
                        url=page.url,
                        title=await page.title(),
                        screenshot=f"data:image/png;base64,{data.hex()}",
                    )
                if action.kind is ActionKind.CLICK:
                    await page.click(action.target, timeout=self._timeout_ms)
                    return Observation(kind=action.kind, ok=True, detail=f"clicked {action.target!r}", url=page.url)
                if action.kind is ActionKind.TYPE:
                    await page.fill(action.target, action.text, timeout=self._timeout_ms)
                    # The detail never echoes what was typed.
                    return Observation(
                        kind=action.kind, ok=True, detail=f"typed {len(action.text)} chars", url=page.url
                    )
                return Observation(kind=action.kind, ok=False, error=f"unsupported action {action.kind.value!r}")
            finally:
                await browser.close()


def provider_from_settings() -> ComputerProvider:
    """Build the configured driver. An unknown name is a refusing `NullProvider`.

    Failing closed on a typo matters more than being forgiving: a misspelled
    `computer_provider` must not silently fall back to something that can act.
    """
    # Imported here so `iris_ai.computer` stays importable without settings being
    # fully initialised (tests import the vocabulary directly).
    from iris_ai.config import settings

    name = (settings.computer_provider or "null").strip().lower()
    if name in ("", "null", "none", "off"):
        return NullProvider()
    if name == "playwright":
        return PlaywrightProvider(timeout_seconds=settings.computer_action_timeout_seconds)
    return NullProvider(
        reason=f"unknown computer_provider {name!r}; expected one of null, playwright"
    )
