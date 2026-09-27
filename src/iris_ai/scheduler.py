"""Scheduler — circadian proactivity (the part of Iris that acts, not reacts).

Two jobs, both no-ops when they cannot do their job safely:

- nightly sleep: runs the dream cycle at `nightly_sleep_hour` (local tz) —
  staged signals consolidate into MEMORY.md while the owner sleeps.
- morning brief: at `morning_brief_hour`, sends the owner a Telegram digest —
  what decayed (rot), what dreaming promoted, how memory looks. Requires an
  owner chat id (settings.owner_chat_id) *and* a connected Telegram channel;
  without either it stays silent instead of failing.

Formatting is a pure function so the digest can be tested without a broker.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from iris_ai.agent.runtime import Runtime
from iris_ai.config import settings

log = logging.getLogger("iris_ai.scheduler")

NIGHTLY_JOB_ID = "nightly-sleep"


def owner_sleep_hour(workspace_root: Path) -> int:
    """The owner's chosen dream hour from onboarding (config/iris.json),
    falling back to the settings env var when not set yet."""
    try:
        data = json.loads((workspace_root / "config" / "iris.json").read_text(encoding="utf-8"))
        pref = str(data.get("sleep_pref", "")).strip()
        if pref.isdigit() and 0 <= int(pref) <= 23:
            return int(pref)
    except Exception:  # noqa: BLE001 - a broken config must not kill boot
        pass
    return settings.nightly_sleep_hour


def reschedule_nightly(scheduler: AsyncIOScheduler, hour: int) -> None:
    """Re-register the nightly sleep job at a new hour (no restart needed)."""
    scheduler.reschedule_job(
        NIGHTLY_JOB_ID,
        trigger=CronTrigger(hour=hour, minute=0),
        misfire_grace_time=3600,
    )
    log.info("nightly sleep rescheduled to hour %s", hour)


def format_morning_brief(retention: dict, rot: dict, dreams: dict | None = None) -> str:
    chunks = retention.get("chunks", [])
    total = len(chunks)
    avg = round(sum(c.get("retention", 1.0) for c in chunks) / total, 2) if total else 1.0
    lines = ["Good morning. Night cycle report:"]
    if dreams:
        lines.append(
            f"- Dreaming: {dreams.get('promoted', 0)} promoted, "
            f"{dreams.get('superseded', 0)} superseded, {dreams.get('themes', 0)} themes"
        )
    lines.append(f"- Memory: {total} chunks, average retention {avg:.2f}")
    low = [c for c in chunks if c.get("retention", 1.0) < 0.5]
    if low:
        lines.append(
            f"- {len(low)} memories below 50% retention — oldest: "
            f"{low[0]['content'][:60]!r}"
        )
    rot_entries = rot.get("entries", [])
    if rot_entries:
        lines.append(f"- {rot.get('count')} flagged as rot (see /rot to review)")
    if not chunks:
        lines.append("- Memory is empty — say something today and I'll start remembering.")
    return "\n".join(lines)


async def _nightly_sleep(runtime: Runtime) -> None:
    try:
        record = await runtime.dreams.sleep()
        await runtime.reindexer.reindex_all()
        log.info("nightly sleep done: promoted=%d", record.promoted)
    except Exception as exc:  # noqa: BLE001 - a failed night must not crash the process
        log.warning("nightly sleep failed: %s", exc)


def _last_dream(dreams_md: str) -> dict | None:
    """Pull the last dream cycle's headline numbers from DREAMS.md.

    The brief's dreams section always passed None before — the dream record
    was never surfaced, so the owner's morning digest never mentioned the
    night's consolidation."""
    import re

    entries = re.findall(
        r"- staged=(\d+) promoted=(\d+) themes=(\d+) added=(\d+) superseded=(\d+)",
        dreams_md,
    )
    if not entries:
        return None
    staged, promoted, themes, added, superseded = entries[-1]
    return {
        "staged": int(staged),
        "promoted": int(promoted),
        "themes": int(themes),
        "added": int(added),
        "superseded": int(superseded),
    }


async def _morning_brief(runtime: Runtime) -> None:
    if not settings.owner_chat_id or runtime.telegram is None:
        log.info("morning brief skipped (owner chat id or telegram channel missing)")
        return
    try:
        retention = await runtime.forgetting.retention_report()
        rot = await runtime.forgetting.rot_report()
        dreams = None
        dreams_md = runtime.files.read(runtime.files.dreams)
        if dreams_md:
            dreams = _last_dream(dreams_md)
        brief = format_morning_brief(
            {"chunks": retention},
            {
                "count": len(rot),
                "entries": [
                    {"content": e.content, "retention": e.retention, "age_days": e.age_days}
                    for e in rot
                ],
            },
            dreams,
        )
        await runtime.telegram.send_message(settings.owner_chat_id, brief)
        log.info("morning brief sent to owner")
    except Exception as exc:  # noqa: BLE001
        log.warning("morning brief failed: %s", exc)


def build_scheduler(runtime: Runtime) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.iris_timezone)
    scheduler.add_job(
        _nightly_sleep,
        CronTrigger(hour=owner_sleep_hour(runtime.files.root), minute=0),
        args=[runtime],
        id=NIGHTLY_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _morning_brief,
        CronTrigger(hour=settings.morning_brief_hour, minute=0),
        args=[runtime],
        id="morning-brief",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler
