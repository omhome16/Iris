"""`Computer` — the single choke point every screen action passes through.

The tool in `iris.agent.tools` owns the LangGraph approval interrupt (that is
where the graph context lives). Everything *else* — is this action permitted
here, does it need approval, is there budget left, do it, record it — lives here,
behind one `execute()`.

That shape is deliberate. There is exactly one place an action can be performed,
and exactly one place it is recorded, so "every action produces exactly one audit
record" is true by construction rather than by remembering to call the logger.
The order is fixed and matches the audit's pre-tool guard order:

    allowlist → confirmation → budget → perform → record

`approve` is injected because approving means interrupting a graph turn, and this
module stays graph-free so it can be tested without one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from iris.computer.actions import Action, Observation
from iris.computer.audit import ActionLog
from iris.computer.permissions import Decision, PermissionModel
from iris.computer.provider import ComputerProvider, provider_from_settings

# Returns True to proceed, False when the owner cancelled.
Approve = Callable[[Action, Decision], Awaitable[bool]]


@dataclass
class Computer:
    """Provider + permission model + audit log, composed."""

    provider: ComputerProvider
    permissions: PermissionModel
    log: ActionLog

    def availability(self) -> tuple[bool, str]:
        return self.provider.availability()

    async def perform(self, action: Action) -> Observation:
        """Do the action via the provider. Never raises; does not record."""
        ok, reason = self.provider.availability()
        if not ok:
            return Observation(kind=action.kind, ok=False, error=reason, unavailable=True)
        try:
            return await self.provider.perform(action)
        except Exception as exc:  # noqa: BLE001 - a driver must never break a turn
            return Observation(
                kind=action.kind, ok=False, error=f"{self.provider.name} driver failed: {exc}"
            )

    async def execute(
        self,
        action: Action,
        *,
        session: str = "",
        approve: Approve | None = None,
    ) -> Observation:
        """The one path: driver, allowlist, confirm, budget, perform, record."""
        # A missing driver is answered first: there is no point asking the owner
        # to approve an action nothing can carry out.
        available, reason = self.provider.availability()
        if not available:
            return self._finish(
                action,
                Observation(kind=action.kind, ok=False, error=reason, unavailable=True),
                session,
                "unavailable",
            )

        decision = self.permissions.check(action)
        if decision.refused:
            return self._finish(action, self._refusal(action, decision.reason), session, "refused")

        needs_approval = self.permissions.grants.remaining(session) <= 0 or self.permissions.needs_confirmation(
            action
        )
        if needs_approval:
            if approve is None or not await approve(action, decision):
                return self._finish(
                    action, self._refusal(action, "cancelled by the owner"), session, "cancelled"
                )
            # A fresh approval refills the budget; a confirmation inside a live
            # grant does not, so confirming does not extend the sequence.
            if self.permissions.grants.remaining(session) <= 0:
                self.permissions.grants.grant(session)

        if not self.permissions.grants.consume(session):
            return self._finish(
                action,
                self._refusal(action, "action budget exhausted — approve again to continue"),
                session,
                "budget_exhausted",
            )

        observation = await self.perform(action)
        return self._finish(action, observation, session, "allowed")

    def _refusal(self, action: Action, reason: str) -> Observation:
        return Observation(kind=action.kind, ok=False, error=reason)

    def _finish(self, action: Action, observation: Observation, session: str, decision: str) -> Observation:
        self.log.record(action, observation, session=session, decision=decision)
        return observation


def computer_from_settings(workspace_root: Path) -> Computer:
    """Build the configured `Computer` (driver, allowlists, audit log)."""
    from iris.config import settings

    permissions = PermissionModel.from_csv(
        allowed_hosts=settings.computer_allowed_hosts,
        allowed_apps=settings.computer_allowed_apps,
        max_actions=settings.computer_max_actions,
        confirm_destructive=settings.computer_confirm_destructive,
    )
    return Computer(
        provider=provider_from_settings(),
        permissions=permissions,
        log=ActionLog(Path(workspace_root) / "config" / "actions.jsonl"),
    )
