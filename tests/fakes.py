"""Shared fakes for the Iris test suite.

WizardLLM drives the LLM-based onboarding wizard deterministically: each
turn it learns one more profile field (the first turn takes the owner's name
verbatim from what they said), so onboarding completes in exactly five
answers without any network call. Its complete_with_tools returns a plain
reply so the same fake works for ordinary chat turns after onboarding.

FakeJev stands in for the TypeSafe client: scripted answers, no network, and a
record of every request so tests can assert the *batching* contract (one
request carrying many questions) rather than one call per candidate.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from iris.jev.client import JevAnswers
from iris.memory.llm import LLMClient


class WizardLLM(LLMClient):
    def __init__(self) -> None:
        self.turns = 0

    async def complete(self, messages, **kwargs):
        self.turns += 1
        user = messages[-1]["content"].strip()
        profile = {}
        # The SYSTEM_PROMPT tells the model to emit profile.name (not
        # owner_name). The fake previously emitted the wrong key, which is
        # exactly why the name-drop bug sailed through the suite.
        if self.turns == 1:
            profile["name"] = user
        if self.turns >= 2:
            profile["personality"] = "warm and curious"
        if self.turns >= 3:
            profile["tone"] = "short and direct"
        if self.turns >= 4:
            profile["timezone"] = "UTC"
        if self.turns >= 5:
            profile["sleep_pref"] = "4"
        complete = self.turns >= 5
        return json.dumps(
            {"message": "Done!" if complete else "Next?", "profile": profile, "complete": complete}
        )

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""


class FakeJev:
    """Scripted JEV stand-in.

    Each answer map may hold plain values or callables. A callable receives
    `(key, state)` and returns the answer, so a test can answer the reranker's
    `c0/c1/...` questions positionally.
    """

    def __init__(
        self,
        *,
        nouls: dict[str, float | Callable[[str, Any], float]] | None = None,
        choices: dict[str, str] | None = None,
        scores: dict[str, float] | None = None,
        confidences: dict[str, float] | None = None,
        default_noul: float = 0.0,
        default_choice: str = "",
        default_score: float = 0.0,
        default_confidence: float = 1.0,
        fail: bool = False,
    ) -> None:
        self.nouls = nouls or {}
        self.choices = choices or {}
        self.scores = scores or {}
        self.confidences = confidences or {}
        self.default_noul = default_noul
        self.default_choice = default_choice
        self.default_score = default_score
        self.default_confidence = default_confidence
        self.fail = fail
        self.calls: list[dict] = []

    @property
    def enabled(self) -> bool:
        return not self.fail

    async def ask(self, state, questions):
        self.calls.append({"state": state, "questions": dict(questions)})
        if self.fail:
            return None

        def _resolve(mapping, key, default):
            value = mapping.get(key, default)
            return value(key, state) if callable(value) else value

        nouls = {
            key: float(_resolve(self.nouls, key, self.default_noul))
            for key, q in questions.items()
            if q.get("type") == "noul"
        }
        choices = {
            key: str(_resolve(self.choices, key, self.default_choice))
            for key, q in questions.items()
            if q.get("type") == "choice"
        }
        scores = {
            key: float(_resolve(self.scores, key, self.default_score))
            for key, q in questions.items()
            if q.get("type") == "score"
        }
        confidences = {key: float(self.confidences.get(key, self.default_confidence)) for key in questions}
        return JevAnswers(
            nouls=nouls, choices=choices, scores=scores, confidences=confidences, model="jev-fake"
        )

    def question_ids(self) -> list[str]:
        return [key for call in self.calls for key in call["questions"]]
