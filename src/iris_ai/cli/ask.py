"""Arrow-key and numbered prompts for `iris init`.

No extra dependency. A real terminal gets up/down and Enter. A pipe, a test,
or `IRIS_PLAIN` gets numbered choices. `ScriptedPrompter` answers from a dict
so the flow can be tested without a keyboard.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Protocol


class Prompter(Protocol):
    def say(self, text: str) -> None: ...

    def select(self, message: str, choices: list[tuple[str, str]], *, default: str = "") -> str: ...

    def text(self, message: str, *, default: str = "") -> str: ...

    def secret(self, message: str) -> str: ...

    def confirm(self, message: str, *, default: bool = True) -> bool: ...

    def checkbox(self, message: str, choices: list[tuple[str, str]]) -> list[str]: ...


@dataclass
class ScriptedPrompter:
    """Answers keyed by the question text. Missing keys use the default."""

    answers: dict[str, object] = field(default_factory=dict)
    said: list[str] = field(default_factory=list)

    def say(self, text: str) -> None:
        self.said.append(text)

    def select(self, message: str, choices: list[tuple[str, str]], *, default: str = "") -> str:
        picked = self.answers.get(message, default or (choices[0][0] if choices else ""))
        return str(picked)

    def text(self, message: str, *, default: str = "") -> str:
        return str(self.answers.get(message, default))

    def secret(self, message: str) -> str:
        return str(self.answers.get(message, ""))

    def confirm(self, message: str, *, default: bool = True) -> bool:
        value = self.answers.get(message, default)
        return bool(value)

    def checkbox(self, message: str, choices: list[tuple[str, str]]) -> list[str]:
        picked = self.answers.get(message, [])
        return [str(item) for item in picked] if isinstance(picked, (list, tuple)) else []


class PlainPrompter:
    """Numbered choices. Works in any terminal, including a pipe."""

    def say(self, text: str) -> None:
        print(text)

    def select(self, message: str, choices: list[tuple[str, str]], *, default: str = "") -> str:
        print(f"\n{message}")
        names = [value for value, _label in choices]
        current = names.index(default) if default in names else 0
        for index, (_value, label) in enumerate(choices, start=1):
            mark = ">" if index - 1 == current else " "
            print(f"  {mark} {index}. {label}")
        raw = input(f"choice [{current + 1}]: ").strip()
        if not raw:
            return names[current]
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        if raw in names:
            return raw
        print(f"  using {names[current]}")
        return names[current]

    def text(self, message: str, *, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        raw = input(f"{message}{hint}: ").strip()
        return raw or default

    def secret(self, message: str) -> str:
        import getpass

        return getpass.getpass(f"{message}: ")

    def confirm(self, message: str, *, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        raw = input(f"{message} [{hint}]: ").strip().lower()
        if not raw:
            return default
        return raw in {"y", "yes"}

    def checkbox(self, message: str, choices: list[tuple[str, str]]) -> list[str]:
        print(f"\n{message}")
        print("  Enter numbers separated by commas, or press Enter for none.")
        for index, (_value, label) in enumerate(choices, start=1):
            print(f"    {index}. {label}")
        raw = input("include: ").strip()
        if not raw:
            return []
        picked: list[str] = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(choices):
                picked.append(choices[int(part) - 1][0])
        return picked


class ArrowPrompter(PlainPrompter):
    """Up/down to move, Enter to accept. Falls back to numbers when it cannot."""

    def select(self, message: str, choices: list[tuple[str, str]], *, default: str = "") -> str:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return super().select(message, choices, default=default)
        names = [value for value, _label in choices]
        index = names.index(default) if default in names else 0
        while True:
            _draw(message, [label for _value, label in choices], index)
            key = _read_key()
            if key == "up":
                index = (index - 1) % len(choices)
            elif key == "down":
                index = (index + 1) % len(choices)
            elif key in {"enter", "quit"}:
                _draw.lines = 0  # type: ignore[attr-defined]
                print()
                return names[index]


def prompter_for() -> Prompter:
    if os.environ.get("IRIS_PLAIN") or not sys.stdin.isatty():
        return PlainPrompter()
    return ArrowPrompter()


def _draw(message: str, labels: list[str], index: int) -> None:
    # Move up over the previous frame and clear it. The line count is visual
    # (a wrapped prompt is more than one row) and is reset when the menu ends,
    # so the next question does not climb into the previous prompt.
    if getattr(_draw, "lines", 0):
        sys.stdout.write(f"\x1b[{_draw.lines}A\x1b[J")  # type: ignore[attr-defined]
    frame = [message, "  up/down, enter to accept"]
    frame.extend(f"  {'>' if i == index else ' '} {label}" for i, label in enumerate(labels))
    written = "\n".join(frame) + "\n"
    sys.stdout.write(written)
    sys.stdout.flush()
    _draw.lines = _visual_lines(written)  # type: ignore[attr-defined]


def _visual_lines(text: str) -> int:
    import shutil

    width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns)
    count = 0
    for line in text.splitlines() or [""]:
        count += max(1, (len(line) + width - 1) // width)
    return count


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in {"\r", "\n"}:
            return "enter"
        if ch in {"\x00", "\xe0"}:
            nxt = msvcrt.getwch()
            if nxt == "H":
                return "up"
            if nxt == "P":
                return "down"
        if ch == "\x03":
            return "quit"
        return ""
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch in {"\r", "\n"}:
            return "enter"
        if ch == "\x03":
            return "quit"
        if ch == "\x1b":
            rest = sys.stdin.read(2)
            if rest == "[A":
                return "up"
            if rest == "[B":
                return "down"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return ""
