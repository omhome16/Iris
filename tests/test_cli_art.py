"""The start screen: generated art, and every path that must skip it.

The art is a *function*, so these are property tests rather than golden files —
they survive a taste change, and they fail on the things that actually break a
terminal banner: ragged rows, characters outside the ramp, colour in a pipe, a
banner drawn into a log file, or an exception on an 8-column window.
"""

from __future__ import annotations

from typer.testing import CliRunner

from iris_ai.cli import art
from iris_ai.cli.main import app

runner = CliRunner()


# ── the drawing ─────────────────────────────────────────────────────────


def test_every_row_is_exactly_the_requested_width():
    """Ragged rows are the failure mode of hand-written ASCII art."""
    for width, height in (art.WIDE, art.COMPACT, (31, 9), (97, 23)):
        rows = art.art_rows(width, height)
        assert len(rows) == height
        assert {len(row) for row in rows} == {width}


def test_the_art_is_deterministic():
    assert art.art_rows(*art.WIDE) == art.art_rows(*art.WIDE)


def test_only_ramp_or_space_characters_are_emitted():
    """Anything outside the ramp means a glyph the terminal may not have."""
    allowed = set(art.RAMP) | {" "}
    for row in art.art_rows(*art.WIDE):
        assert set(row) <= allowed


def test_the_mark_is_actually_drawn():
    """A regression that flattens the pattern would still satisfy the row-width
    test, so assert there is structure: bright cells, and a left/right balance.

    The balance is not exact — one specular highlight sits off-centre, which is
    what stops it looking like a printed logo — but a hand-edited or mis-sampled
    grid drifts far more than the highlight does.
    """
    rows = art.art_rows(*art.WIDE)
    assert any("@" in row for row in rows)
    mirrored = sum(1 for row in rows for a, b in zip(row, row[::-1], strict=True) if a != b)
    cells = sum(len(row) for row in rows)
    assert mirrored > 0, "the highlight is deliberate, not a rendering bug"
    assert mirrored < cells * 0.02


def test_a_tiny_grid_does_not_raise():
    rows = art.art_rows(8, 3)
    assert rows and all(len(row) == 8 for row in rows)


# ── fitting the terminal ────────────────────────────────────────────────


def test_a_narrow_terminal_gets_the_compact_mark():
    from rich.console import Console

    assert art.size_for(Console(width=60)) == art.COMPACT
    assert art.size_for(Console(width=200)) == art.WIDE
    # Whatever rich reports for an unknown width, it must be one of the two
    # marks rather than a third, wrapped, layout.
    assert art.size_for(Console(width=None)) in (art.WIDE, art.COMPACT)


# ── when not to draw ────────────────────────────────────────────────────


def _terminal(*, width: int = 100) -> object:
    from rich.console import Console
    from rich.file_proxy import FileProxy  # noqa: F401  (import proves the path exists)

    return Console(width=width, force_terminal=True)


def test_banner_is_off_when_the_output_is_not_a_terminal(monkeypatch):
    from rich.console import Console

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("IRIS_NO_BANNER", raising=False)
    captured = Console(width=100)  # rich reports is_terminal False when not a tty
    assert art.banner_enabled(captured) is False
    assert art.banner_enabled(_terminal()) is True


def test_banner_is_off_for_the_flag_the_env_and_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("IRIS_NO_BANNER", raising=False)
    terminal = _terminal()

    assert art.banner_enabled(terminal, no_banner=True) is False

    monkeypatch.setenv("IRIS_NO_BANNER", "1")
    assert art.banner_enabled(terminal) is False
    monkeypatch.setenv("IRIS_NO_BANNER", "0")
    assert art.banner_enabled(terminal) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert art.banner_enabled(terminal) is False


def test_colour_off_means_no_escape_codes():
    plain = art.art_text(*art.COMPACT, color=False).plain
    assert "\x1b" not in plain
    assert plain.startswith(art.art_rows(*art.COMPACT)[0][:8])


def test_coloured_text_carries_the_dawn_ramp():
    text = art.art_text(*art.COMPACT, color=True)
    styles = {span.style for span in text.spans}
    assert styles, "a coloured mark must actually be styled"
    assert all(str(style).startswith("#") for style in styles)  # hex rgb, from the ramp
    # Runs are merged: far fewer spans than painted characters, so a line is not
    # emitted one character at a time.
    painted = sum(1 for char in text.plain if char not in " \n")
    assert len(text.spans) < painted <= len(text.plain)


# ── wiring ──────────────────────────────────────────────────────────────


def test_the_start_screen_still_prints_the_real_commands(monkeypatch):
    """The banner is decoration; the help under it is the reference."""
    monkeypatch.setenv("IRIS_NO_BANNER", "1")
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    for name in ("chat", "doctor", "guards", "skills", "tools"):
        assert name in result.stdout


def test_iris_chat_accepts_no_banner():
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    assert "--no-banner" in result.stdout
