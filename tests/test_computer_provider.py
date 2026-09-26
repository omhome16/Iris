"""The driver boundary: a missing driver refuses, and a driver never raises."""

from __future__ import annotations

from iris_ai.computer import Action, ActionKind, NullProvider, PlaywrightProvider, provider_from_settings
from iris_ai.computer.audit import ActionLog
from iris_ai.computer.permissions import PermissionModel
from iris_ai.computer.provider import PLAYWRIGHT_MISSING
from iris_ai.computer.session import Computer
from iris_ai.config import settings


def _computer(provider, tmp_path, **permissions) -> Computer:
    return Computer(
        provider=provider,
        permissions=PermissionModel(**permissions),
        log=ActionLog(tmp_path / "config" / "actions.jsonl"),
    )


def test_the_null_provider_refuses_and_says_why():
    ok, reason = NullProvider().availability()
    assert ok is False
    assert "no driver" in reason


async def test_the_null_provider_refuses_rather_than_raising():
    observation = await NullProvider().perform(Action(ActionKind.SCREENSHOT))
    assert observation.refused
    assert observation.unavailable


def test_settings_default_provider_is_the_null_driver(monkeypatch):
    monkeypatch.setattr(settings, "computer_provider", "null")
    assert provider_from_settings().name == "null"


def test_unknown_provider_fails_closed_with_a_searchable_reason(monkeypatch):
    monkeypatch.setattr(settings, "computer_provider", "chromium")
    provider = provider_from_settings()
    ok, reason = provider.availability()
    assert ok is False
    assert "chromium" in reason


def test_playwright_reports_the_same_reason_every_time():
    """No playwright in CI, so this is the unavailable path — and the reason is a
    single stable string, which is what makes a refusal searchable."""
    provider = PlaywrightProvider()
    ok, reason = provider.availability()
    if ok:  # a developer machine that happens to carry the extra
        assert reason == ""
    else:
        assert reason == PLAYWRIGHT_MISSING
        assert provider.availability() == (False, PLAYWRIGHT_MISSING)  # cached and stable


async def test_unavailable_driver_refuses_before_asking_for_approval(tmp_path):
    asked: list[Action] = []

    async def approve(action, decision):
        asked.append(action)
        return True

    observation = await _computer(NullProvider(), tmp_path).execute(
        Action(ActionKind.NAVIGATE, target="https://example.com"), session="s", approve=approve
    )
    assert observation.unavailable
    assert asked == []  # nothing to approve when there is no driver


async def test_a_driver_that_raises_never_raises_into_the_graph(tmp_path):
    class Exploding:
        name = "boom"

        def availability(self):
            return True, ""

        async def perform(self, action):
            raise RuntimeError("browser died")

    observation = await _computer(Exploding(), tmp_path).perform(Action(ActionKind.SCREENSHOT))
    assert observation.refused
    assert "boom" in observation.error
