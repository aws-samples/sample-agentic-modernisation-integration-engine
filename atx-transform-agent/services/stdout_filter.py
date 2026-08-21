"""De-noiser for ATX CLI stdout.

**This is a deliberate duplicate of ``atx-analysis-agent/services/command_service.py``
(the ``strip_ansi`` / ``visible_text`` / ``despinner`` / ``is_noise`` / ``StdoutFilter``
group).** The two ATX agents ship as separate containers with separate dependency
closures, so a shared import is not available across the package boundary and the
duplication is forced. design.md ("ATX Agent Streaming and Reconnect Contract")
states that a second, differently-shaped streaming design in one of the two ATX
agents is a defect, not a variant — so this file is kept recognisably identical to
its counterpart rather than reinvented. Change one, change both.

It is the same ATX CLI producing the same noise in both agents: ANSI colour codes,
Braille spinner frames, box-drawing banners, and a progress block that is repainted
many times per second.
"""

import re
from collections import deque

# ANSI CSI / OSC escape sequences emitted by the ATX CLI's progress rendering.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")

# Continuation marker the ATX CLI uses for the lines below its spinner.
_PROGRESS_MARKER = "⋮"


def _is_spinner(char: str) -> bool:
    """True if ``char`` is a Braille spinner frame (⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ and friends).

    The whole Braille Patterns block is treated as a spinner: the ATX CLI uses it
    only for progress animation, never for content.
    """
    return 0x2800 <= ord(char) <= 0x28FF


def _is_decoration(char: str) -> bool:
    """True if ``char`` is spinner/box-drawing/block decoration rather than content."""
    if char.isspace() or _is_spinner(char):
        return True
    code = ord(char)
    # Box Drawing (2500–257F), Block Elements (2580–259F), Geometric Shapes (25A0–25FF)
    return 0x2500 <= code <= 0x25FF


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from ``text``."""
    return _ANSI_RE.sub("", text)


def visible_text(raw: str) -> str:
    """Reduce a raw stdout line to what a terminal would actually show.

    Strips ANSI escapes and honours carriage-return overwrites by keeping only
    the last segment written to the line.
    """
    clean = strip_ansi(raw).replace("\x08", "")
    segments = [segment for segment in clean.split("\r") if segment.strip()]
    return segments[-1].rstrip() if segments else ""


def despinner(text: str) -> tuple[str, bool]:
    """Split a spinner frame off the front of a progress line.

    Returns ``(text_without_spinner, was_a_progress_frame)``.
    """
    stripped = text.lstrip()
    was_progress = False
    while stripped and _is_spinner(stripped[0]):
        stripped = stripped[1:].lstrip()
        was_progress = True
    return stripped, was_progress


def is_noise(text: str) -> bool:
    """True if ``text`` carries no readable content (spinner/banner/blank).

    Bordered lines that carry text (``│ Region: us-east-1 │``) are content and are
    kept — only pure decoration is dropped.
    """
    stripped = text.strip()
    if not stripped:
        return True
    return all(_is_decoration(char) for char in stripped)


class StdoutFilter:
    """Stateful de-noiser for ATX CLI stdout.

    Call it with a raw stdout line; it returns the payload to keep, or ``None`` to
    drop the line.

    The CLI repaints a multi-line progress block (a spinner line plus one or more
    ``⋮`` continuation lines) many times per second. Because the subprocess is
    read in universal-newline mode, every repaint arrives as fresh lines. A short
    memory of recently emitted progress lines collapses the repaint cycle to one
    entry per actual state change, while any non-progress line clears the memory
    so real content — including genuinely repeated content such as two identical
    ``ERROR:`` lines — is never suppressed.
    """

    def __init__(self, memory: int = 6) -> None:
        self._recent: deque[str] = deque(maxlen=memory)

    def __call__(self, raw: str) -> str | None:
        visible = visible_text(raw)
        if not visible or is_noise(visible):
            return None

        content, was_spinner = despinner(visible)
        if not content:
            return None

        if not (was_spinner or content.lstrip().startswith(_PROGRESS_MARKER)):
            self._recent.clear()
            return visible

        if content in self._recent:
            return None
        self._recent.append(content)
        return content
