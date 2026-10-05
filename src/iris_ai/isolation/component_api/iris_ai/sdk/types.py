"""The JSON-shaped values a hosted component returns. Kept free of the kernel."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextBlock:
    title: str
    text: str
    source: str = ""
    kind: str = "memory"
    priority: int = 50

    def render(self) -> str:
        if not self.title:
            return self.text
        return f"## {self.title}\n{self.text}"


@dataclass(frozen=True, slots=True)
class ContextResult:
    blocks: tuple[ContextBlock, ...] = ()
    skills: tuple[str, ...] = ()

    def render(self) -> str:
        return "\n\n".join(block.render() for block in self.blocks if block.text or block.title)
