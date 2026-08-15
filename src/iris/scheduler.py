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

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from iris.agent.runtime import Runtime
from iris.config import settings

log = logging.getLogger("iris.scheduler")


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


async def _morning_brief(runtime: Runtime) -> None:
    if not settings.owner_chat_id or runtime.telegram is None:
        log.info("morning brief skipped (owner chat id or telegram channel missing)")
        return
    try:
        retention = await runtime.forgetting.retention_report()
        rot = await runtime.forgetting.rot_report()
        dreams = None
        brief = format_morning_brief(retention, rot, dreams)
        await runtime.telegram.send_message(settings.owner_chat_id, brief)
        log.info("morning brief sent to owner")
    except Exception as exc:  # noqa: BLE001
        log.warning("morning brief failed: %s", exc)


def build_scheduler(runtime: Runtime) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.iris_timezone)
    scheduler.add_job(
        _nightly_sleep,
        CronTrigger(hour=settings.nightly_sleep_hour, minute=0),
        args=[runtime],
        id="nightly-sleep",
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