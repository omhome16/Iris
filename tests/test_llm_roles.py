"""Regression tests for the role leakage that broke every provider at once.

LangChain labels its messages `type == "human"` / `"ai"`; providers expect
`user` / `assistant`. `RoleRunner` forwarded the LangChain name verbatim, so the
critic's very first call reached Gemini as `{"role": "human"}`, which LiteLLM
rejected, which every fallback provider then rejected too — a crash whose
message named nothing in this repo. These tests pin both ends of the fix: the
runner no longer leaks the alias, and the provider boundary cannot be crossed
with one either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.agents.handoff import REFUSED_ERROR
from iris_ai.agents.roles import CRITIC
from iris_ai.agents.runner import RoleRunner
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.llm import LLMClient, LLMError, _sampling_kwargs, _to_provider_messages
from test_agent_graph import make_runtime

_VALID_ROLES = {"system", "user", "assistant", "tool", "function", "developer"}


def test_boundary_maps_langchain_aliases() -> None:
    out = _to_provider_messages(
        [
            {"role": "system", "content": "s"},
            {"role": "human", "content": "h"},
            {"role": "ai", "content": "a"},
        ]
    )
    assert [m["role"] for m in out] == ["system", "user", "assistant"]
    # Content is preserved, and the original list is not mutated in place.
    assert out[1]["content"] == "h"


def test_boundary_rejects_an_unknown_role() -> None:
    with pytest.raises(LLMError, match="'robot' is not a provider role"):
        _to_provider_messages([{"role": "robot", "content": "x"}])


def test_gemini_3_samples_at_the_provider_default() -> None:
    """Iris defaults to 0.2, but Gemini 3 deprecates temperature and warns that
    below 1.0 can cause infinite loops — so it must not be sent at all."""
    assert _sampling_kwargs("gemini/gemini-3.5-flash", 0.2) == {}
    assert _sampling_kwargs("gemini/gemini-3.1-flash-lite", 0.2) == {}
    # Gemini 2 and every other provider keep the configured temperature.
    assert _sampling_kwargs("gemini/gemini-2.0-flash", 0.2) == {"temperature": 0.2}
    assert _sampling_kwargs("groq/openai/gpt-oss-120b", 0.2) == {"temperature": 0.2}


class _ExplodingLLM(LLMClient):
    async def complete_with_tools(self, messages, tools=None, **kwargs):
        raise RuntimeError("provider exploded")


async def test_a_broken_run_is_refused_not_silently_empty(tmp_path: Path):
    """A crashed specialist must not look like one that found nothing."""
    files = WorkspaceFiles(tmp_path)
    runner = RoleRunner(make_runtime(files, _ExplodingLLM()), CRITIC)

    handoff = await runner.run("draft")

    assert handoff.refused == REFUSED_ERROR
    assert handoff.claims == ()


class _CapturingLLM(LLMClient):
    """Records the roles it is offered, then reports immediately (no tools)."""

    def __init__(self) -> None:
        self.role_lists: list[list[str]] = []

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.role_lists.append([m["role"] for m in messages])
        return "report", [], ""


async def test_role_runner_never_hands_a_langchain_role_to_the_model(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    llm = _CapturingLLM()
    runner = RoleRunner(make_runtime(files, llm), CRITIC)

    handoff = await runner.run("Draft answer: something. Check it.")

    assert handoff.claims, "the run should have produced a report"
    assert llm.role_lists, "the model should have been called"
    for roles in llm.role_lists:
        assert "human" not in roles and "ai" not in roles, roles
        assert set(roles) <= _VALID_ROLES, roles
        # The role's prompt is the system message; the question is the first
        # non-system message and must arrive as `user`.
        assert roles[0] == "system", roles
        assert roles[1] == "user", roles
