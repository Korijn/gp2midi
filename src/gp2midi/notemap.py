"""Choosing the MIDI note for each drum articulation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

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
