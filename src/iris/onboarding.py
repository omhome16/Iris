"""Onboarding — the birth of Iris's identity.

First-run wizard (LLM-driven): a natural conversation in which the model
gently gathers name → personality → tone → timezone → sleep preference, then
extracts them into the profile. Progress persists in `workspace/config/iris.json`;
the completed profile is written to USER.md with owner provenance.

Why LLM-driven: the owner may say anything ("hi, I'm Omar" / "call me Aria")
instead of answering a rigid question. The model responds in context, infers
what it safely can (name, tone, personality), and only asks for what truly
needs a human answer (timezone, sleep hour). Responses are structured JSON so
profile extraction is deterministic; the chat text is always the model's own.

Fresh-start guarantee: after `scripts/fresh_start.py` (or a wiped workspace),
`onboarded` is False and the chat graph routes every message through the
wizard until it completes — Iris starts with *zero memory but instructions
intact* (AGENTS.md is never touched).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from iris.config import settings
from iris.memory.files import WorkspaceFiles
from iris.text import text_of

log = logging.getLogger("iris.onboarding")

OPENING = "Hi! I'm Iris. Let's get to know each other — what should I call you?"


def _parse_json_object(raw: str) -> dict | None:
    """Robust JSON extraction: free-tier models often wrap JSON in prose or
    markdown fences. Strips the fences and pulls the first balanced {...}."""
    if not raw:
        return None
    text = raw.strip()
    # drop markdown code fences if present
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.strip()
    # find the first { and match its closing brace
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    data = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return data if isinstance(data, dict) else None
    return None

# Every reply the model makes is JSON with `message` (what to say next) and
# `profile` (fields learned so far). `complete` flips true when the model is
# confident it has everything.
SYSTEM_PROMPT = """You are Iris, being born. Have a warm, natural conversation with your owner and gather five profile fields for your memory:
1. name — what to call them
2. personality — how you should be (e.g. warm and curious, dry and efficient, playful)
3. tone — how your messages should sound (e.g. short and direct, friendly and detailed)
4. timezone — their timezone (e.g. Asia/Kolkata, UTC, America/New_York)
5. sleep_pref — what hour (0-23) they'd like your nightly dream consolidation

Ask for them naturally, one or two at a time, in flowing conversation — never list them mechanically and never repeat what you already know. Infer name, personality and tone from what the owner says when you safely can. Ask explicitly for the timezone and sleep hour, because guessing them is unreliable. If the owner answers several things at once, use them.

You may receive your existing memory below (profile already on file and long-term memory). If a field is already known from your memory, CONFIRM it in your message instead of asking again. Only ask for what is genuinely unknown.

Respond with ONLY JSON, no prose around it:
{"message": "what you say to the owner next", "profile": {"name": "", "personality": "", "tone": "", "timezone": "", "sleep_pref": ""}, "complete": false}

Fill in every profile field you have learned so far (empty string for the rest). Set "complete": true only when all five are known, and make "message" a warm closing line."""

REQUIRED = ("name", "timezone", "sleep_pref")  # the fields a guess can't be trusted for

# Profile keys → OnboardingState attribute names.
_STATE_ATTRS = {"name": "owner_name", "timezone": "timezone", "sleep_pref": "sleep_pref"}


@dataclass(slots=True)
class OnboardingState:
    onboarded: bool = False
    step: int = 0  # conversational turns completed (informational only)
    asked: bool = False  # whether the opening question has been asked yet
    history: list = field(default_factory=list)  # [{role, content}]
    owner_name: str = ""
    personality: str = ""
    tone: str = ""
    timezone: str = ""
    sleep_pref: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"


class OnboardingWizard:
    def __init__(self, files: WorkspaceFiles, llm=None) -> None:
        self.files = files
        self.llm = llm
        self.config_path = files.config_file()
        self.state = self._load()

    def _load(self) -> OnboardingState:
        if not self.config_path.exists():
            return OnboardingState()
        try:
            return OnboardingState(**json.loads(self.config_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return OnboardingState()

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(self.state.to_json(), encoding="utf-8")

    @property
    def onboarded(self) -> bool:
        return self.state.onboarded

    def current_prompt(self) -> str:
        """What to show next, or the completion greeting."""
        if self.state.onboarded:
            return f"Welcome back, {self.state.owner_name or 'friend'}."
        if self.state.history:
            return self.state.history[-1]["content"]
        return OPENING

    def _messages(self) -> list[dict]:
        """System prompt + memory consultation + current progress. The model
        must see what is already gathered so it never re-asks a known field."""
        known: list[str] = []
        profile = self.files.read(self.files.user)
        if profile.strip():
            known.append(f"## Owner profile already on file\n{profile.strip()[:1500]}")
        memory = self.files.read(self.files.memory)
        if memory.strip():
            known.append(f"## Existing long-term memory (tail)\n{memory.strip()[-1200:]}")
        partial = {
            "name": self.state.owner_name or "",
            "personality": self.state.personality or "",
            "tone": self.state.tone or "",
            "timezone": self.state.timezone or "",
            "sleep_pref": self.state.sleep_pref or "",
        }
        gathered = {k: v for k, v in partial.items() if v}
        if gathered:
            known.append("## Already gathered this conversation (do not ask again)\n" + json.dumps(gathered))
        system = SYSTEM_PROMPT
        if known:
            system += "\n\n" + "\n\n".join(known)
        return [{"role": "system", "content": system}, *self.state.history]

    def _apply_profile(self, profile: dict) -> None:
        """Apply model-extracted profile fields.

        The model is told to output `profile.name` (see SYSTEM_PROMPT); the
        state attribute is `owner_name`. Map it explicitly — a mismatch here
        silently dropped the owner's name and looped onboarding forever.
        """
        profile = dict(profile or {})
        name = (profile.pop("name", None) or profile.pop("owner_name", None) or "").strip()
        if name:
            self.state.owner_name = name
        for key in ("personality", "tone", "timezone", "sleep_pref"):
            value = (profile.get(key) or "").strip()
            if value:
                setattr(self.state, key, value)

    def _missing(self) -> list[str]:
        return [k for k in REQUIRED if not getattr(self.state, _STATE_ATTRS[k])]

    def greet(self) -> str:
        """First contact: ask the opening question once. The next message is
        the first real answer (the LLM drives everything after that)."""
        if self.state.onboarded:
            return self.current_prompt()
        if not self.state.asked:
            self.state.asked = True
            self.save()
        return OPENING

    def _prompt_for_missing(self) -> str:
        missing = self._missing()
        if not missing:
            return "Tell me more!"
        labels = {"name": "your name", "timezone": "your timezone", "sleep_pref": "what hour to run my nightly dreams"}
        return f"Almost done — I just need {', '.join(labels[k] for k in missing)}."

    def _infer_from_answer(self, answer: str) -> None:
        """Safety-net extraction, never a substitute for the model's judgment:
        if the owner's literal answer clearly supplies a missing field, take
        it. An explicit statement (\"my name is X\") overrides an earlier guess."""
        answer = (answer or "").strip()
        if not answer:
            return
        low = answer.lower()
        # Name: explicit statements override earlier bare-word guesses.
        name_m = re.search(
            r"\b(?:my name is|i am|i'm|call me|you can call me|name's|name is)\s+([A-Za-z][\w'\-]*)\b",
            answer,
            re.IGNORECASE,
        )
        if name_m:
            self.state.owner_name = name_m.group(1).title()
        elif not self.state.owner_name and " " not in answer and len(answer) <= 20 \
                and re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", answer):
            self.state.owner_name = answer.title()
        if not self.state.personality:
            for kw, val in (
                ("warm", "warm and curious"),
                ("curious", "warm and curious"),
                ("playful", "playful"),
                ("funny", "playful"),
                ("serious", "serious"),
                ("dry", "dry and efficient"),
                ("efficient", "dry and efficient"),
                ("calm", "calm and steady"),
                ("chill", "calm and steady"),
            ):
                if kw in low:
                    self.state.personality = val
                    break
        if not self.state.tone:
            for kw, val in (
                ("short", "short and direct"),
                ("direct", "short and direct"),
                ("concise", "short and direct"),
                ("detailed", "friendly and detailed"),
                ("friendly", "friendly and detailed"),
                ("casual", "casual"),
                ("formal", "formal"),
            ):
                if kw in low:
                    self.state.tone = val
                    break
        if not self.state.timezone:
            tz_m = re.search(
                r"\b(?:timezone(?: is)?|tz(?: is)?|time zone(?: is)?)\s*[:=]?\s*([A-Za-z]+(?:/[A-Za-z_+-]+)?)\b",
                answer,
                re.IGNORECASE,
            )
            if tz_m:
                self.state.timezone = tz_m.group(1)
            else:
                # bare timezone answer or any region/city token pair
                m = re.search(r"\b(UTC|GMT|EST|EDT|CST|CDT|MST|MDT|PST|PDT|IST|CET|CEST|EET|AEST|JST|KST)(?:[+-]\d{1,2})?\b", answer, re.IGNORECASE)
                if m:
                    self.state.timezone = m.group(1).upper()
                else:
                    parts = [p.strip() for p in answer.split() if "/" in p]
                    if parts:
                        self.state.timezone = parts[0]
        if not self.state.sleep_pref:
            # Require a time signal — bare numbers ("I'm 18 years old") must
            # not be mistaken for a sleep hour. Accepts "at 4", "9pm",
            # "22:00", "midnight-5" style answers with an explicit marker.
            h_m = re.search(
                r"\b(?:at|around|about|by)\s+((?:2[0-3]|1[0-9]|[1-9])(?::\d{2})?)\b"
                r"|\b((?:2[0-3]|1[0-9]|[1-9])(?::\d{2})?)\s*(?:am|pm|o'?clock|hours?)\b",
                low,
            )
            if h_m:
                hour = int((h_m.group(1) or h_m.group(2)).split(":")[0])
                if hour <= 23:
                    self.state.sleep_pref = str(hour)

    async def _ask_model(self) -> tuple[str, bool]:
        """One LLM turn. Returns (message, done). Never raises: onboarding
        must not hard-fail the chat turn."""
        if self.llm is None:
            return self._prompt_for_missing(), False
        try:
            raw = await self.llm.complete(
                self._messages(), tier="strong", json_mode=True, max_tokens=1500
            )
            data = _parse_json_object(raw) or {}
        except Exception as exc:  # noqa: BLE001 - LLM hiccups degrade gracefully
            log.warning("onboarding LLM turn failed: %s", exc)
            data = {}
        self._apply_profile(data.get("profile") or {})
        missing = self._missing()
        if data.get("complete") and not missing:
            return (data.get("message") or "").strip(), True
        msg = (data.get("message") or "").strip()
        return (msg or self._prompt_for_missing()), False

    async def apply_answer(self, answer: str) -> str:
        """Record the owner's message, let the model reply, persist. Returns
        what the model said next (or the completion greeting)."""
        if self.state.onboarded:
            return self.current_prompt()
        answer = text_of(answer)
        self.state.history.append({"role": "user", "content": answer.strip()})
        self.state.step += 1
        message, done = await self._ask_model()
        self._infer_from_answer(answer)  # deterministic backstop for missing fields
        if done and not self._missing():
            if message:
                self.state.history.append({"role": "assistant", "content": message})
            self.save()
            return self._finalize()
        if not message:
            message = self._prompt_for_missing()
        self.state.history.append({"role": "assistant", "content": message})
        self.save()
        return message

    def _finalize(self) -> str:
        self._write_user_profile()
        self._wire_runtime()
        self.state.onboarded = True
        self.save()
        name = self.state.owner_name or "friend"
        return (
            f"Done — I'm yours, {name}. Your profile is written to USER.md "
            "(owner provenance), and my dream cycle is set for hour "
            f"{self.state.sleep_pref or 4}. What's on your mind?"
        )

    def _wire_runtime(self) -> None:
        """Make the answers the owner gave during onboarding actually drive
        runtime behavior: the scheduler's dream hour and the tz used for
        daily notes, traces, and persona. Previously the timezone/sleep hour
        were written to USER.md but never applied."""
        tz = (self.state.timezone or "").strip()
        if tz:
            try:
                ZoneInfo(tz)
                settings.iris_timezone = tz
            except Exception:  # noqa: BLE001 - bad tz string, keep default
                log.warning("ignoring invalid timezone from onboarding: %r", tz)
        hour = (self.state.sleep_pref or "").strip()
        if hour:
            try:
                h = int(hour.split(":")[0])
                if 0 <= h <= 23:
                    settings.nightly_sleep_hour = h
            except ValueError:
                log.warning("ignoring invalid sleep hour from onboarding: %r", hour)

    def _write_user_profile(self) -> None:
        lines = [
            "# USER.md — the owner's profile",
            "",
            "> Written during onboarding. Owner provenance. Refined by dreaming.",
            "",
            f"- Name: {self.state.owner_name or '(not given)'}",
            f"- Personality preference: {self.state.personality or '(not given)'}",
            f"- Message tone: {self.state.tone or '(not given)'}",
            f"- Timezone: {self.state.timezone or 'UTC'}",
            f"- Dream consolidation hour: {self.state.sleep_pref or '4'}",
            f"- Onboarded: {datetime.now().isoformat(timespec='seconds')}",
            "",
        ]
        self.files.write_curated(self.files.user, "\n".join(lines))
