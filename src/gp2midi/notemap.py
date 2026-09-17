"""Choosing the MIDI note for each drum articulation, and how choked cymbals are exported."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction

from .gpif import Articulation


def normalize(name: str) -> str:
    """Case- and whitespace-insensitive form of an articulation name (Guitar Pro itself is
    not consistent: "Bongo high (hit)" in one file is "Bongo High (hit)" in another)."""
    return "/".join(" ".join(part.split()).casefold() for part in name.split("/"))


def qualified_name(articulation: Articulation) -> str:
    return f"{articulation.element}/{articulation.name}"


@dataclass
class NoteMap:
    """MIDI notes chosen for articulations, by articulation name ("Snare (rim shot)") or,
    when instruments share an articulation name, by instrument and articulation
    ("Ride Cymbal 2/Ride (bell)"); the latter wins. A note of None leaves the articulation
    out. Articulations not listed keep Guitar Pro's own (General MIDI) note."""

    entries: dict[str, int | None] = field(default_factory=dict)
    _lookup: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._lookup = {normalize(key): key for key in self.entries}

    def match(self, articulation: Articulation) -> str | None:
        """The entry that applies to ``articulation``, if any."""
        for name in (qualified_name(articulation), articulation.name):
            key = self._lookup.get(normalize(name))
            if key is not None:
                return key
        return None

    def note(self, articulation: Articulation) -> int | None:
        key = self.match(articulation)
        if key is not None:
            return self.entries[key]
        return articulation.output_midi if 0 <= articulation.output_midi <= 127 else None

    def unknown(self, articulations: Iterable[Articulation]) -> list[str]:
        """Entries that name none of ``articulations``, typically typos."""
        names = set()
        for a in articulations:
            names.add(normalize(a.name))
            names.add(normalize(qualified_name(a)))
        return [key for key in self.entries if normalize(key) not in names]


CHOKE = "(choke)"  # Guitar Pro names its choke articulations "Crash medium (choke)" and so on


@dataclass
class ChokeMap:
    """How a choked cymbal is exported. Guitar Pro gives a choke the same MIDI note as an
    ordinary hit, so the choke is lost; drum instruments want either a second note that
    chokes the cymbal, or polyphonic aftertouch on the cymbal's own note.

    ``notes`` overrides the choking note per articulation, like [notes] does; a note of None
    leaves that cymbal unchoked, and naming an articulation there makes it count as a choke
    even when Guitar Pro does not call it one.
    """

    mode: str = "note"  # "note", "aftertouch" or "off"
    at: Fraction | None = None  # in quarter notes after the hit; None: at the end of the ring
    velocity: int = 100  # of the choking note
    pressure: int = 127  # aftertouch value
    notes: NoteMap = field(default_factory=NoteMap)

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def is_choke(self, articulation: Articulation) -> bool:
        return self.notes.match(articulation) is not None or CHOKE in normalize(articulation.name)

    def key(self, articulation: Articulation) -> int | None:
        """The note that chokes this articulation, or None if it is not choked. Unlisted
        articulations use the number Guitar Pro itself gives them (94-98 for the cymbals),
        which is free in a General MIDI drum map."""
        if not self.enabled or not self.is_choke(articulation):
            return None
        entry = self.notes.match(articulation)
        if entry is not None:
            return self.notes.entries[entry]
        return articulation.input_midi if 0 <= articulation.input_midi <= 127 else None
