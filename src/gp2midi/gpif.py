"""Reading Guitar Pro 7/8 files (.gp).

A .gp file is a zip archive; the score lives in ``Content/score.gpif``, an XML
document in which musical objects are stored in flat lists and reference each
other by id::

    MasterBar -> Bar (one per staff) -> Voice -> Beat -> Note
                                                 Beat -> Rhythm

Identical objects are shared, so one ``<Note>`` can be used by many beats.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from pathlib import Path

# Note values expressed in quarter notes.
NOTE_VALUES = {
    "DoubleWhole": Fraction(8),
    "Whole": Fraction(4),
    "Half": Fraction(2),
    "Quarter": Fraction(1),
    "Eighth": Fraction(1, 2),
    "16th": Fraction(1, 4),
    "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16),
    "128th": Fraction(1, 32),
    "256th": Fraction(1, 64),
}

# Tempo automations carry a reference unit; converts the value to quarter-note BPM.
TEMPO_REFERENCE = {1: 0.5, 2: 1.0, 3: 1.5, 4: 2.0, 5: 3.0}

# Bit flags of <Note><Accent>.
ACCENT_STACCATO = 0x01
ACCENT_HEAVY = 0x04
ACCENT_NORMAL = 0x08
ACCENT_TENUTO = 0x10


class Accent(Enum):
    NONE = "none"
    ACCENT = "accent"
    HEAVY = "heavy accent"


class Grace(Enum):
    NONE = "none"
    BEFORE_BEAT = "BeforeBeat"
    ON_BEAT = "OnBeat"


class GPFormatError(Exception):
    pass


@dataclass(frozen=True)
class Articulation:
    element: str
    name: str
    input_midi: int
    output_midi: int


@dataclass(frozen=True)
class Note:
    articulation: int  # index into Track.articulations (drum kits only)
    midi: int | None  # the note's "Midi" property; for drums the *input* number
    accent: Accent
    ghost: bool
    tie_destination: bool
    staccato: bool = False


@dataclass(frozen=True)
class Beat:
    duration: Fraction  # in quarter notes
    dynamic: str  # PPP, PP, P, MP, MF, F, FF, FFF
    grace: Grace
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class Bar:
    voices: tuple[tuple[Beat, ...], ...]
    simile: str | None = None


@dataclass(frozen=True)
class TempoPoint:
    position: float  # fraction of the bar, 0..1
    bpm: float  # quarter notes per minute
    # Ramp linearly from this point to the next one, rather than changing at once. Guitar
    # Pro's MIDI export drops the ramp, so this was checked against its playback by ear.
    linear: bool


@dataclass
class MasterBar:
    index: int
    numerator: int
    denominator: int
    bars: list[Bar]  # one per staff, in track order
    repeat_start: bool = False
    repeat_end: bool = False
    repeat_count: int = 0  # total number of plays when repeat_end is set
    alternate_endings: frozenset[int] = frozenset()
    section: str | None = None
    tempo: list[TempoPoint] = field(default_factory=list)
    has_directions: bool = False

    @property
    def duration(self) -> Fraction:
        return Fraction(4 * self.numerator, self.denominator)


@dataclass
class Track:
    index: int
    name: str
    instrument_type: str
    staff_offset: int  # index of this track's first staff in MasterBar.bars
    articulations: list[Articulation]

    @property
    def number(self) -> int:
        """1-based position in the track list, as shown to users."""
        return self.index + 1

    @property
    def is_drumkit(self) -> bool:
        return self.instrument_type == "drumKit"

    def bar(self, master_bar: MasterBar) -> Bar:
        return master_bar.bars[self.staff_offset]


@dataclass
class Score:
    title: str
    artist: str
    tracks: list[Track]
    master_bars: list[MasterBar]

    @property
    def drum_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.is_drumkit]


def read_gpif(path: str | Path) -> bytes:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read("Content/score.gpif")
    except (zipfile.BadZipFile, KeyError) as e:
        raise GPFormatError(
            f"{path}: not a Guitar Pro 7/8 file (expected a zip with Content/score.gpif)"
        ) from e


def load(path: str | Path) -> Score:
    try:
        return parse_gpif(read_gpif(path))
    except ET.ParseError as e:
        raise GPFormatError(f"{path}: damaged score ({e})") from e


def parse_gpif(data: bytes) -> Score:
    return _Parser(ET.fromstring(data)).parse()


def _text(el: ET.Element | None, path: str, default: str = "") -> str:
    if el is None:
        return default
    value = el.findtext(path)
    return value.strip() if value is not None else default


def _ids(el: ET.Element, path: str) -> list[str]:
    return (el.findtext(path) or "").split()


class _Parser:
    def __init__(self, root: ET.Element):
        self.root = root
        self.bar_els = self._index("Bars")
        self.voice_els = self._index("Voices")
        self.beat_els = self._index("Beats")
        self.note_els = self._index("Notes")
        self.rhythm_els = self._index("Rhythms")
        self.beats: dict[str, Beat] = {}
        self.notes: dict[str, Note] = {}
        self.rhythms: dict[str, Fraction] = {}

    def _index(self, name: str) -> dict[str, ET.Element]:
        container = self.root.find(name)
        if container is None:
            return {}
        return {el.get("id"): el for el in container}

    def parse(self) -> Score:
        score_el = self.root.find("Score")
        return Score(
            title=_text(score_el, "Title"),
            artist=_text(score_el, "Artist"),
            tracks=self._tracks(),
            master_bars=self._master_bars(),
        )

    def _tracks(self) -> list[Track]:
        tracks = []
        staff_offset = 0
        for index, el in enumerate(self.root.findall("Tracks/Track")):
            articulations = [
                Articulation(
                    element=_text(element, "Name"),
                    name=_text(art, "Name"),
                    input_midi=int((_text(art, "InputMidiNumbers").split() or ["-1"])[0]),
                    output_midi=int(_text(art, "OutputMidiNumber") or "-1"),
                )
                for element in el.findall("InstrumentSet/Elements/Element")
                for art in element.findall("Articulations/Articulation")
            ]
            tracks.append(
                Track(
                    index=index,
                    name=_text(el, "Name"),
                    instrument_type=_text(el, "InstrumentSet/Type"),
                    staff_offset=staff_offset,
                    articulations=articulations,
                )
            )
            staff_offset += max(1, len(el.findall("Staves/Staff")))
        return tracks

    def _master_bars(self) -> list[MasterBar]:
        tempo_by_bar: dict[int, list[TempoPoint]] = {}
        for auto in self.root.findall("MasterTrack/Automations/Automation"):
            if _text(auto, "Type") != "Tempo":
                continue
            value, *reference = _text(auto, "Value").split()
            ref = int(reference[0]) if reference else 2
            tempo_by_bar.setdefault(int(_text(auto, "Bar", "0")), []).append(
                TempoPoint(
                    position=float(_text(auto, "Position", "0")),
                    bpm=float(value) * TEMPO_REFERENCE.get(ref, 1.0),
                    linear=_text(auto, "Linear").lower() == "true",
                )
            )

        master_bars = []
        for index, el in enumerate(self.root.findall("MasterBars/MasterBar")):
            numerator, denominator = (int(x) for x in _text(el, "Time", "4/4").split("/"))
            mb = MasterBar(
                index=index,
                numerator=numerator,
                denominator=denominator,
                bars=[self._bar(bar_id) for bar_id in _ids(el, "Bars")],
                tempo=sorted(tempo_by_bar.get(index, []), key=lambda p: p.position),
                has_directions=el.find("Directions") is not None,
            )
            repeat = el.find("Repeat")
            if repeat is not None:
                mb.repeat_start = repeat.get("start", "").lower() == "true"
                mb.repeat_end = repeat.get("end", "").lower() == "true"
                mb.repeat_count = int(repeat.get("count") or 2) if mb.repeat_end else 0
            endings = _text(el, "AlternateEndings")
            if endings:
                mb.alternate_endings = frozenset(int(x) for x in endings.split())
            section = el.find("Section")
            if section is not None:
                parts = [_text(section, "Letter"), _text(section, "Text")]
                mb.section = " ".join(p for p in parts if p) or None
            master_bars.append(mb)
        return master_bars

    def _bar(self, bar_id: str) -> Bar:
        el = self.bar_els[bar_id]
        voices = tuple(
            tuple(self._beat(beat_id) for beat_id in _ids(self.voice_els[voice_id], "Beats"))
            for voice_id in _ids(el, "Voices")
            if voice_id != "-1"
        )
        return Bar(voices=voices, simile=el.findtext("SimileMark"))

    def _beat(self, beat_id: str) -> Beat:
        if beat_id not in self.beats:
            el = self.beat_els[beat_id]
            grace = _text(el, "GraceNotes")
            self.beats[beat_id] = Beat(
                duration=self._rhythm(el.find("Rhythm").get("ref")),
                dynamic=_text(el, "Dynamic", "MF"),
                grace=Grace(grace) if grace in ("BeforeBeat", "OnBeat") else Grace.NONE,
                notes=tuple(self._note(note_id) for note_id in _ids(el, "Notes")),
            )
        return self.beats[beat_id]

    def _note(self, note_id: str) -> Note:
        if note_id not in self.notes:
            el = self.note_els[note_id]
            flags = int(_text(el, "Accent", "0"))
            if flags & ACCENT_HEAVY:
                accent = Accent.HEAVY
            elif flags & ACCENT_NORMAL:
                accent = Accent.ACCENT
            else:
                accent = Accent.NONE
            tie = el.find("Tie")
            midi = el.findtext("Properties/Property[@name='Midi']/Number")
            self.notes[note_id] = Note(
                articulation=int(_text(el, "InstrumentArticulation", "-1")),
                midi=int(midi) if midi is not None else None,
                accent=accent,
                ghost=_text(el, "AntiAccent").lower() == "normal",
                tie_destination=tie is not None and tie.get("destination", "").lower() == "true",
                staccato=bool(flags & ACCENT_STACCATO),
            )
        return self.notes[note_id]

    def _rhythm(self, rhythm_id: str) -> Fraction:
        if rhythm_id not in self.rhythms:
            el = self.rhythm_els[rhythm_id]
            duration = NOTE_VALUES[_text(el, "NoteValue", "Quarter")]
            dot = el.find("AugmentationDot")
            dots = int(dot.get("count", "0")) if dot is not None else 0
            # each dot adds half of the previous addition: 1.5x, 1.75x, ...
            duration *= 2 - Fraction(1, 2**dots)
            for tag in ("PrimaryTuplet", "SecondaryTuplet"):
                tuplet = el.find(tag)
                if tuplet is not None:
                    duration *= Fraction(int(tuplet.get("den")), int(tuplet.get("num")))
            self.rhythms[rhythm_id] = duration
        return self.rhythms[rhythm_id]
