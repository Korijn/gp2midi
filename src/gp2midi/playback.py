"""Turning the notated score into a timeline of played events.

All positions are in quarter notes from the start of the song, as exact fractions.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .gpif import Beat, GPFormatError, Grace, MasterBar, Score, Track
from .notemap import ChokeMap, NoteMap
from .velocity import VelocityMap

DEFAULT_BPM = 120.0


@dataclass(frozen=True)
class PlayedBar:
    master_bar: MasterBar
    start: Fraction


@dataclass
class NoteEvent:
    start: Fraction
    end: Fraction
    key: int
    velocity: int
    choke: int | None = None  # a choked cymbal: the note that chokes it (see ChokeMap)


def playback_order(master_bars: list[MasterBar], expand_repeats: bool = True) -> list[int]:
    """Indices of master bars in the order they are played, following repeats and
    alternate endings."""
    if not expand_repeats:
        return [mb.index for mb in master_bars]
    order = []
    start = 0  # first bar of the current repeat section
    play = 1  # current pass through the section (1-based)
    in_endings = False
    i = 0
    while i < len(master_bars):
        mb = master_bars[i]
        if mb.repeat_start and i != start:
            start, play = i, 1
        if mb.alternate_endings:
            in_endings = True
            if play not in mb.alternate_endings:
                i += 1
                continue
        elif in_endings:
            # left the alternate endings: the next repeat section starts here
            in_endings = False
            start, play = i, 1
        order.append(i)
        if len(order) > 100 * len(master_bars):
            raise GPFormatError("repeats could not be resolved (endless loop)")
        if mb.repeat_end:
            if play < mb.repeat_count:
                play += 1
                i = start
                in_endings = False
                continue
            if not mb.alternate_endings:
                start, play = i + 1, 1
        i += 1
    return order


def played_bars(score: Score, expand_repeats: bool = True) -> list[PlayedBar]:
    result = []
    position = Fraction(0)
    for index in playback_order(score.master_bars, expand_repeats):
        mb = score.master_bars[index]
        result.append(PlayedBar(mb, position))
        position += mb.duration
    return result


def tempo_map(
    bars: list[PlayedBar], step: Fraction = Fraction(1, 8), ramps: bool = True
) -> list[tuple[Fraction, float]]:
    """(position, bpm) changes. Gradual (linear) tempo changes are approximated by a tempo
    change every ``step`` quarter notes, each using the ramp's tempo at the middle of its
    step; with ``ramps`` off they become one jump, the way Guitar Pro's own export writes
    them."""
    points = [
        (pb.start + pb.master_bar.duration * Fraction(p.position).limit_denominator(100_000), p)
        for pb in bars
        for p in pb.master_bar.tempo
    ]
    changes: list[tuple[Fraction, float]] = []

    def add(position: Fraction, bpm: float) -> None:
        if changes and changes[-1][0] == position:
            changes.pop()
        if not changes or changes[-1][1] != bpm:
            changes.append((position, bpm))

    if not points or points[0][0] > 0:
        add(Fraction(0), DEFAULT_BPM)
    for i, (position, point) in enumerate(points):
        if not (ramps and point.linear and i + 1 < len(points)):
            add(position, point.bpm)
            continue
        end, target = points[i + 1]
        steps = max(1, int((end - position) / step))
        for k in range(steps):
            middle = (k + Fraction(1, 2)) / steps
            add(position + (end - position) * k / steps, point.bpm + (target.bpm - point.bpm) * float(middle))
    return changes


def drum_events(
    track: Track,
    bars: list[PlayedBar],
    velocities: VelocityMap,
    notes: NoteMap | None = None,
    note_length: Fraction | None = None,
    chokes: ChokeMap | None = None,
) -> list[NoteEvent]:
    """``note_length`` (in quarter notes) replaces the written note lengths when given."""
    notes = notes or NoteMap()
    chokes = chokes or ChokeMap(mode="off")
    events: list[NoteEvent] = []
    last_by_voice_key: dict[tuple[int, int], NoteEvent] = {}
    previous_beat: dict[int, list[NoteEvent]] = {}  # per voice, what grace notes steal from

    def emit(voice: int, beat: Beat, start: Fraction, duration: Fraction, is_grace: bool) -> list[NoteEvent]:
        created = []
        for note in beat.notes:
            if not 0 <= note.articulation < len(track.articulations):
                continue
            articulation = track.articulations[note.articulation]
            key = notes.note(articulation)
            if key is None:
                continue
            previous = last_by_voice_key.get((voice, key))
            if note.tie_destination and previous is not None:
                if note_length is None:
                    previous.end = max(previous.end, start + duration)
                continue
            length = note_length if note_length is not None else duration / 2 if note.staccato else duration
            velocity = velocities.velocity(beat.dynamic, note, is_grace)
            event = NoteEvent(start, start + length, key, velocity, chokes.key(articulation))
            events.append(event)
            created.append(event)
            last_by_voice_key[voice, key] = event
        return created

    for pb in bars:
        for v, voice in enumerate(track.bar(pb.master_bar).voices):
            position = pb.start
            graces: list[Beat] = []
            for beat in voice:
                if beat.grace is not Grace.NONE:
                    graces.append(beat)  # grace beats take no time in the bar
                    continue
                start, duration = position, beat.duration
                if graces:
                    stolen = sum((g.duration for g in graces), Fraction(0))
                    if graces[0].grace is Grace.BEFORE_BEAT:
                        # played before the beat, taking their time from the beat before it
                        grace_start = max(start - stolen, Fraction(0))
                        for event in previous_beat.get(v, ()):
                            event.end = min(event.end, grace_start)
                    else:
                        # played on the beat, taking their time from the beat itself
                        grace_start = start
                        start += stolen
                        duration = duration - stolen if stolen < duration else duration / 2
                    for g in graces:
                        emit(v, g, grace_start, g.duration, is_grace=True)
                        grace_start += g.duration
                    graces = []
                previous_beat[v] = emit(v, beat, start, duration, is_grace=False)
                position += beat.duration
            for g in graces:  # dangling grace notes at the end of a voice
                emit(v, g, position, g.duration, is_grace=True)

    # A note must not sound past the next hit on the same key, otherwise its note-off
    # would cut that hit short (overlapping voices, before-beat grace notes). Of two
    # simultaneous hits on the same key only the loudest (sorted last) survives.
    events.sort(key=lambda e: (e.start, e.key, e.velocity))
    next_start: dict[int, Fraction] = {}
    for event in reversed(events):
        if event.key in next_start:
            event.end = min(event.end, next_start[event.key])
        next_start[event.key] = event.start
    return [e for e in events if e.end > e.start]
