"""Writing played events to a Standard MIDI File (type 1)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import mido

from .playback import NoteEvent, PlayedBar

Timed = tuple[int, int, mido.Message | mido.MetaMessage]


@dataclass
class DrumPart:
    name: str
    events: list[NoteEvent]


def microseconds_per_quarter(bpm: float) -> int:
    # truncated rather than rounded, like Guitar Pro's own export, so both stay in sync
    return int(60_000_000 / bpm)


def _track(name: str, timed: list[Timed]) -> mido.MidiTrack:
    """Build a track from (tick, priority, message) tuples; at equal ticks lower priority
    goes first, so a note-off always precedes a note-on of the same key."""
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    now = 0
    for tick, _, msg in sorted(timed, key=lambda t: (t[0], t[1])):
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def build_midi(
    title: str,
    bars: list[PlayedBar],
    tempo: list[tuple[Fraction, float]],
    parts: list[DrumPart],
    channel: int = 9,
    ticks_per_quarter: int = 960,
    markers: bool = True,
) -> mido.MidiFile:
    """``channel`` is 0-based: 9 is General MIDI's drum channel 10."""

    def ticks(position: Fraction) -> int:
        return round(position * ticks_per_quarter)

    conductor: list[Timed] = []
    for position, bpm in tempo:
        # tempo changes sit at a fraction of a bar and rarely land on a tick; Guitar Pro
        # rounds those down, so follow it and stay in step with its own export
        at = int(position * ticks_per_quarter)
        conductor.append((at, 1, mido.MetaMessage("set_tempo", tempo=microseconds_per_quarter(bpm))))
    signature = None
    for pb in bars:
        mb = pb.master_bar
        tick = ticks(pb.start)
        if (mb.numerator, mb.denominator) != signature:
            signature = (mb.numerator, mb.denominator)
            conductor.append((tick, 0, mido.MetaMessage("time_signature", numerator=mb.numerator, denominator=mb.denominator)))
        if markers and mb.section:
            conductor.append((tick, 2, mido.MetaMessage("marker", text=mb.section)))

    # Guitar Pro writes names as UTF-8 too; mido's default Latin-1 fails on other characters
    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_quarter, charset="utf-8")
    midi.tracks.append(_track(title or "Tempo", conductor))
    for part in parts:
        messages: list[Timed] = []
        for e in part.events:
            start = ticks(e.start)
            # at a coarse resolution a very short note could round to zero length, and its
            # note-off (sorted first) would then leave the note hanging
            end = max(ticks(e.end), start + 1)
            messages.append((start, 1, mido.Message("note_on", channel=channel, note=e.key, velocity=e.velocity)))
            messages.append((end, 0, mido.Message("note_off", channel=channel, note=e.key, velocity=0)))
        midi.tracks.append(_track(part.name, messages))
    return midi


def save(midi: mido.MidiFile, path: str | Path) -> None:
    midi.save(str(path))
