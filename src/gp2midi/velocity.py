"""Mapping Guitar Pro dynamics and note markings to MIDI velocities.

Each marking (normal, accent, heavy accent, ghost) has a velocity for each of the eight
dynamics. Grace notes are offset from the velocity of their own marking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .gpif import Accent, Note

DYNAMICS = ("PPP", "PP", "P", "MP", "MF", "F", "FF", "FFF")
MARKINGS = ("normal", "accent", "heavy_accent", "ghost")

# (ppp, fff) per marking, spread evenly over the dynamics in between. fff with a heavy
# accent reaches 127 exactly, so no two markings collapse onto the ceiling.
DEFAULT_RANGES = {
    "normal": (33, 103),
    "accent": (45, 115),
    "heavy_accent": (57, 127),
    # narrow: a ghost note stays a ghost note, however loud the passage
    "ghost": (20, 55),
}
DEFAULT_GRACE_OFFSET = -20

# What Guitar Pro's own MIDI export does: the dynamic alone sets the velocity, in steps of
# a tenth of 127 (ppp is 20%, fff 90%). Measured from its exports of three songs, which
# between them use every dynamic but ppp.
GUITAR_PRO = tuple(round(127 * (2 + i) / 10) for i in range(len(DYNAMICS)))


def spread(low: int, high: int) -> tuple[int, ...]:
    """Velocities for ppp..fff, evenly spaced from ``low`` to ``high``."""
    steps = len(DYNAMICS) - 1
    return tuple(int(low + (high - low) * i / steps + 0.5) for i in range(steps + 1))


def marking(note: Note) -> str:
    """The marking that decides a note's velocity. A ghost note counts as a ghost note
    even when it is also accented."""
    if note.ghost:
        return "ghost"
    if note.accent is Accent.HEAVY:
        return "heavy_accent"
    if note.accent is Accent.ACCENT:
        return "accent"
    return "normal"


def _default_table() -> dict[str, tuple[int, ...]]:
    return {name: spread(*DEFAULT_RANGES[name]) for name in MARKINGS}


@dataclass
class VelocityMap:
    table: dict[str, tuple[int, ...]] = field(default_factory=_default_table)
    grace_offset: int = DEFAULT_GRACE_OFFSET
    markings: bool = True  # False: ignore markings and use Guitar Pro's velocities

    def velocity(self, dynamic: str, note: Note, is_grace: bool = False) -> int:
        dynamic = dynamic.upper()
        index = DYNAMICS.index(dynamic if dynamic in DYNAMICS else "MF")
        if not self.markings:
            return GUITAR_PRO[index]
        value = self.table[marking(note)][index]
        if is_grace:
            value += self.grace_offset
        return max(1, min(127, value))
