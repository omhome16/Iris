"""Shared fakes for the Iris test suite.

WizardLLM drives the LLM-based onboarding wizard deterministically: each
turn it learns one more profile field (the first turn takes the owner's name
verbatim from what they said), so onboarding completes in exactly five
answers without any network call. Its complete_with_tools returns a plain
reply so the same fake works for ordinary chat turns after onboarding.
"""

from __future__ import annotations

import json

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