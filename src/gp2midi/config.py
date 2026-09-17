"""Settings: built-in defaults, optionally overridden by a gp2midi.toml file.

Every setting is optional. Without a file, every drum track is exported with the default
velocities and Guitar Pro's General MIDI notes.
"""

from __future__ import annotations

import codecs
import json
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from .gpif import Articulation
from .notemap import NoteMap, normalize, qualified_name
from .velocity import DYNAMICS, MARKINGS, VelocityMap, spread

FILENAME = "gp2midi.toml"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    tracks: list[str | int] | None = None  # names and/or numbers (from 1); None: every drum track
    repeats: bool = True
    output: str = "{name}.mid"
    channel: int = 10  # 1-16
    ticks_per_quarter: int = 960
    note_length: Fraction | None = None  # in quarter notes; None keeps the written lengths
    markers: bool = True
    tempo_ramps: bool = True
    velocity: VelocityMap = field(default_factory=VelocityMap)
    notes: NoteMap = field(default_factory=NoteMap)
    source: Path | None = field(default=None, compare=False)


def find(explicit: str | Path | None = None) -> Config:
    """Settings from ``explicit``, else from gp2midi.toml in the current folder, else defaults."""
    if explicit is not None:
        return load(explicit)
    if Path(FILENAME).is_file():
        return load(FILENAME)
    return Config()


def load(path: str | Path) -> Config:
    path = Path(path).absolute()
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e.strerror}") from e
    try:
        data = tomllib.loads(_decode(raw))
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: not a UTF-8 text file") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(data, path.name, source=path)


def _decode(raw: bytes) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        # what `gp2midi config > gp2midi.toml` produces in Windows PowerShell 5
        return raw.decode("utf-16")
    return raw.decode("utf-8")


def parse(data: dict[str, Any], label: str = FILENAME, source: Path | None = None) -> Config:
    config = Config(source=source)
    top = _Table(data, label)

    config.tracks = _tracks(top)
    config.repeats = top.get_bool("repeats", config.repeats)

    output = top.table("output")
    config.output = _output_path(output, config.output)
    output.finish()

    midi = top.table("midi")
    config.channel = midi.get_int("channel", config.channel, 1, 16)
    config.ticks_per_quarter = midi.get_int("ticks_per_quarter", config.ticks_per_quarter, 1, 32767)
    config.note_length = _note_length(midi)
    config.markers = midi.get_bool("markers", config.markers)
    config.tempo_ramps = midi.get_bool("tempo_ramps", config.tempo_ramps)
    midi.finish()

    velocity = top.table("velocity")
    config.velocity = _velocity(velocity)
    velocity.finish()

    config.notes = _notes(top.table("notes"))
    top.finish()
    return config


def _show(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class _Table:
    """A TOML table whose settings are taken out one by one, so leftovers can be reported."""

    def __init__(self, data: dict[str, Any], label: str, name: str = ""):
        self.data = dict(data)
        self.label = label  # file name
        self.name = name  # table name, "" at the top level

    def error(self, key: str, message: str) -> ConfigError:
        where = f"[{self.name}] " if self.name else ""
        return ConfigError(f"{self.label}: {where}{key}: {message}")

    def table(self, key: str) -> _Table:
        value = self.data.pop(key, {})
        if not isinstance(value, dict):
            raise self.error(key, f"expected a [{key}] section, got {_show(value)}")
        return _Table(value, self.label, key)

    def get_bool(self, key: str, default: bool) -> bool:
        value = self.data.pop(key, default)
        if not isinstance(value, bool):
            raise self.error(key, f"expected true or false, got {_show(value)}")
        return value

    def get_int(self, key: str, default: int, low: int, high: int) -> int:
        value = self.data.pop(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise self.error(key, f"expected a whole number from {low} to {high}, got {_show(value)}")
        return value

    def finish(self) -> None:
        if self.data:
            where = f" in [{self.name}]" if self.name else ""
            raise ConfigError(f"{self.label}: unknown setting{where}: {', '.join(sorted(self.data))}")


def _tracks(top: _Table) -> list[str | int] | None:
    value = top.data.pop("tracks", "auto")
    if isinstance(value, str) and value.strip().casefold() == "auto":
        return None
    items = value if isinstance(value, list) else [value]

    def valid(item: Any) -> bool:
        if isinstance(item, str):
            return bool(item.strip())
        return isinstance(item, int) and not isinstance(item, bool) and item >= 1

    if not items or not all(valid(item) for item in items):
        raise top.error("tracks", f'expected "auto", or track names and/or numbers such as ["Drumkit"] or [6], got {_show(value)}')
    return items


def _output_path(table: _Table, default: str) -> str:
    value = table.data.pop("path", default)
    if not isinstance(value, str) or not value.strip():
        raise table.error("path", f'expected a file path such as "{{name}}.mid", got {_show(value)}')
    try:
        value.format(name="", folder="")
    except (KeyError, IndexError, ValueError, AttributeError):
        raise table.error("path", "only {name} and {folder} can be used between braces") from None
    return value


def _note_length(table: _Table) -> Fraction | None:
    value = table.data.pop("note_length", "written")
    if isinstance(value, str) and value.strip().casefold() == "written":
        return None
    try:
        whole_notes = Fraction(value) if isinstance(value, str) else None
    except (ValueError, ZeroDivisionError):
        whole_notes = None
    if whole_notes is None or whole_notes <= 0:
        raise table.error("note_length", f'expected "written" or a note value such as "1/32", got {_show(value)}')
    return whole_notes * 4


def _velocity(table: _Table) -> VelocityMap:
    defaults = VelocityMap()
    levels = dict(defaults.table)
    for name in MARKINGS:
        if name not in table.data:
            continue
        value = table.data.pop(name)
        if not (
            isinstance(value, list)
            and len(value) in (2, len(DYNAMICS))
            and all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 127 for v in value)
        ):
            raise table.error(name, f"expected [ppp, fff] or eight velocities from ppp to fff, each 1 to 127, got {_show(value)}")
        levels[name] = spread(*value) if len(value) == 2 else tuple(value)
    return VelocityMap(
        table=levels,
        grace_offset=table.get_int("grace_offset", defaults.grace_offset, -126, 126),
        markings=table.get_bool("markings", defaults.markings),
    )


def _notes(table: _Table) -> NoteMap:
    entries: dict[str, int | None] = {}
    seen: dict[str, str] = {}
    for key, value in table.data.items():
        name = _show(key)
        if isinstance(value, dict):
            raise table.error(name, 'put articulation names in quotes, e.g. "Snare (rim shot)" = 40')
        if value is False:
            entries[key] = None
        elif isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 127:
            entries[key] = value
        else:
            raise table.error(name, f"expected a MIDI note number from 0 to 127, or false to leave it out, got {_show(value)}")
        other = seen.setdefault(normalize(key), key)
        if other != key:
            raise table.error(name, f"same articulation as {_show(other)}")
    table.data.clear()
    return NoteMap(entries)


def to_toml(config: Config, articulations: Iterable[Articulation] = ()) -> str:
    """The settings as a commented gp2midi.toml. ``articulations`` are listed under [notes]
    with the note they get, besides any notes already set."""
    v = config.velocity
    note_length = "written" if config.note_length is None else str(config.note_length / 4)
    lines = [
        "# gp2midi settings. Every setting is optional: leave one out to get its default.",
        "",
        '# Tracks to export: "auto" for every drum track, or track names and/or numbers',
        '# as listed by `gp2midi inspect`, e.g. ["Drumkit"] or [6].',
        f"tracks = {_show('auto' if config.tracks is None else config.tracks)}",
        "# Play repeats and alternate endings like Guitar Pro does (false: every bar once).",
        f"repeats = {_show(config.repeats)}",
        "",
        "[output]",
        "# File to write for each Guitar Pro file: {name} is its name without extension,",
        "# {folder} the folder it is in. Relative to where gp2midi runs; -o overrides this.",
        f"path = {_show(config.output)}",
        "",
        "[midi]",
        "# drum channel, 1-16 (10 in General MIDI)",
        f"channel = {config.channel}",
        f"ticks_per_quarter = {config.ticks_per_quarter}",
        '# "written" keeps the notated lengths, or give every note one length, e.g. "1/32"',
        f"note_length = {_show(note_length)}",
        "# section names (Intro, Verse, ...) as markers",
        f"markers = {_show(config.markers)}",
        "# true: gradual tempo changes speed up or slow down bit by bit, as written.",
        "# false: they jump in one step, the way Guitar Pro's own MIDI export writes them.",
        f"tempo_ramps = {_show(config.tempo_ramps)}",
        "",
        "[velocity]",
        "# true: dynamics, accents, ghost notes and grace notes set the velocity, as below.",
        "# false: only dynamics count, with Guitar Pro's own velocities, so notes get the",
        "# same velocity as in Guitar Pro's MIDI export (the settings below are unused).",
        f"markings = {_show(v.markings)}",
        "# Velocity per dynamic, ppp pp p mp mf f ff fff: [ppp, fff] spreads evenly over",
        "# the eight, or list all eight. A ghost note that is also accented counts as ghost.",
        *(f"{name} = {_levels(v.table[name])}" for name in MARKINGS),
        "# grace notes (flams, drags) are this much softer than their own marking",
        f"grace_offset = {v.grace_offset}",
        "",
        "[notes]",
        "# MIDI note per drum articulation, named as `gp2midi inspect` shows them. Unlisted",
        "# articulations keep Guitar Pro's General MIDI note; false leaves one out. When two",
        '# instruments share an articulation name, prefix the instrument: "Ride Cymbal 2/Ride (bell)".',
        *_note_lines(config.notes, articulations),
    ]
    return "\n".join(lines) + "\n"


def _levels(levels: tuple[int, ...]) -> str:
    if levels == spread(levels[0], levels[-1]):
        return f"[{levels[0]}, {levels[-1]}]"
    return "[" + ", ".join(map(str, levels)) + "]"


def _note_lines(notes: NoteMap, articulations: Iterable[Articulation]) -> list[str]:
    used: dict[str, Articulation] = {}
    for a in articulations:
        used.setdefault(normalize(qualified_name(a)), a)
    shared_names = Counter(normalize(a.name) for a in used.values())

    def line(key: str, note: int | None) -> str:
        return f"{_show(key)} = {'false' if note is None else note}"

    lines, written = [], set()
    for a in used.values():
        key = notes.match(a) or (qualified_name(a) if shared_names[normalize(a.name)] > 1 else a.name)
        if normalize(key) not in written:
            written.add(normalize(key))
            lines.append(line(key, notes.note(a)))
    for key, note in notes.entries.items():
        if normalize(key) not in written:
            lines.append(line(key, note))
    return lines or ['# "Snare (rim shot)" = 40']
