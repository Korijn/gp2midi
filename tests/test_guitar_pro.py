"""Checks against real Guitar Pro files, and against Guitar Pro's own MIDI exports of the
same songs where those are available (in a "GP Exports" subfolder next to them).

Point GP2MIDI_TEST_SONGS at the folder holding the .gp files to run these; without it they
are skipped. The songs themselves stay out of this repository.
"""

import os
from collections import Counter, defaultdict
from pathlib import Path

import mido
import pytest

from gp2midi import gpif
from gp2midi.cli import main
from gp2midi.notemap import ChokeMap

# What it takes to get Guitar Pro's own export out of gp2midi.
GUITAR_PRO_SETTINGS = """
[midi]
ticks_per_quarter = 480
tempo_ramps = false
flams_per_drum = false
[velocity]
markings = false
[chokes]
mode = "off"
"""

CHOKES_OFF = '[chokes]\nmode = "off"\n'
# The default velocities, with everything else played the way Guitar Pro plays it.
GUITAR_PRO_TIMING = '[midi]\nflams_per_drum = false\n' + CHOKES_OFF

SONGS = Path(os.environ["GP2MIDI_TEST_SONGS"]) if os.environ.get("GP2MIDI_TEST_SONGS") else None
EXPORTS = SONGS / "GP Exports" if SONGS else None


def songs() -> list[Path]:
    return sorted(SONGS.glob("*.gp")) if SONGS and SONGS.is_dir() else []


def native_export(song: Path) -> Path:
    return EXPORTS / f"{song.stem}.mid"


def choke_keys(song: Path) -> set[int]:
    """The notes that choke a cymbal somewhere in this song, by default."""
    chokes = ChokeMap()
    score = gpif.load(song)
    keys = set()
    for track in score.drum_tracks:
        for mb in score.master_bars:
            for voice in track.bar(mb).voices:
                for beat in voice:
                    for note in beat.notes:
                        if 0 <= note.articulation < len(track.articulations):
                            key = chokes.key(track.articulations[note.articulation])
                            if key is not None:
                                keys.add(key)
    return keys


def has_tempo_ramp(song: Path) -> bool:
    return any(point.linear for mb in gpif.load(song).master_bars for point in mb.tempo)


def cases(paths: list[Path], missing: str):
    """Parametrised songs, or one skipped case when there are none to run."""
    return paths or [pytest.param(None, marks=pytest.mark.skip(reason=f"no {missing} (set GP2MIDI_TEST_SONGS)"))]


def name(song: Path | None) -> str:
    return song.stem if song else "none"


def summary(path: Path):
    """Drum notes as (start tick, end tick, note, velocity), tempo changes (repeats of the
    same tempo dropped: Guitar Pro restates the tempo after a repeat), time signatures, the
    length in seconds, and whether the drums are the last thing playing in the file."""
    mf = mido.MidiFile(path)
    notes, tempo, meter, ends = [], [], [], []
    for track in mf.tracks:
        now, sounding = 0, defaultdict(list)
        for m in track:
            now += m.time
            if m.type == "set_tempo" and (not tempo or tempo[-1][1] != m.tempo):
                tempo.append((now, m.tempo))
            elif m.type == "time_signature":
                meter.append((now, m.numerator, m.denominator))
            elif m.type == "note_on" and m.velocity and m.channel == 9:
                sounding[m.note].append((now, m.velocity))
            elif m.type in ("note_on", "note_off") and m.channel == 9 and sounding[m.note]:
                start, velocity = sounding[m.note].pop(0)
                notes.append((start, now, m.note, velocity))
        ends.append(now)
    drums_end_last = max(ends) == max(end for _, end, _, _ in notes)
    return mf.ticks_per_beat, sorted(notes), tempo, meter, round(mf.length, 6), drums_end_last


@pytest.mark.parametrize("song", cases(songs(), "Guitar Pro files"), ids=name)
def test_real_files_export(isolated_folder, song):
    assert main([str(song), "-o", "out"]) == 0
    mf = mido.MidiFile(isolated_folder / "out" / f"{song.stem}.mid")
    assert len({m.velocity for m in mf.tracks[1] if m.type == "note_on"}) > 3


@pytest.mark.parametrize("song", cases([s for s in songs() if native_export(s).is_file()], "Guitar Pro exports"), ids=name)
def test_identical_to_guitar_pro_export(isolated_folder, song):
    """Guitar Pro's own export is velocity per dynamic only, gradual tempo changes as one
    jump, no sign of a cymbal choke, and 480 ticks per quarter. Told to do the same, gp2midi
    must match it exactly."""
    (isolated_folder / "gp.toml").write_text(GUITAR_PRO_SETTINGS)
    assert main([str(song), "-o", "ours.mid", "--config", "gp.toml"]) == 0
    tpq, notes, tempo, meter, length, _ = summary(isolated_folder / "ours.mid")
    native = summary(native_export(song))
    assert notes == native[1]
    assert (tempo, meter) == (native[2], native[3])
    if native[5]:  # other instruments may play on after the drums stop; then lengths differ
        assert length == native[4]


@pytest.mark.parametrize(
    "song",
    cases([s for s in songs() if native_export(s).is_file() and not has_tempo_ramp(s)], "Guitar Pro exports"),
    ids=name,
)
def test_only_velocities_differ_from_guitar_pro_export(isolated_folder, song):
    """Everything gp2midi adds by default is a setting away; with the markings left on, only
    the velocities may differ from Guitar Pro's own export."""
    (isolated_folder / "plain.toml").write_text(GUITAR_PRO_TIMING)
    assert main(["export", str(song), "-o", "ours.mid", "--config", "plain.toml"]) == 0
    tpq, notes, tempo, meter, length, _ = summary(isolated_folder / "ours.mid")
    native_tpq, native_notes, native_tempo, native_meter, native_length, drums_last = summary(native_export(song))
    scale = tpq // native_tpq
    assert [(s // scale, e // scale, k) for s, e, k, _ in notes] == [(s, e, k) for s, e, k, _ in native_notes]
    assert [(t // scale, v) for t, v in tempo] == native_tempo
    assert [(t // scale, n, d) for t, n, d in meter] == native_meter
    assert len({v for *_, v in notes}) > len({v for *_, v in native_notes})


@pytest.mark.parametrize("song", cases([s for s in songs() if choke_keys(s)], "songs with a choked cymbal"), ids=name)
def test_choked_cymbals_get_a_choking_note(isolated_folder, song):
    """A choked cymbal is exported as the cymbal plus a note that chokes it where the cymbal
    stops ringing; nothing else about the export changes."""
    assert main([str(song), "-o", "choked.mid"]) == 0
    (isolated_folder / "off.toml").write_text(CHOKES_OFF)
    assert main([str(song), "-o", "plain.mid", "--config", "off.toml"]) == 0
    choked = Counter(summary(isolated_folder / "choked.mid")[1])
    plain = Counter(summary(isolated_folder / "plain.mid")[1])
    assert not plain - choked  # every note of the plain export is still there, unchanged
    extra = choked - plain
    assert extra and {key for _, _, key, _ in extra} <= choke_keys(song)
    ends = {end for _, end, _, _ in plain}
    assert all(start in ends for start, *_ in extra)  # where the cymbal stops ringing


@pytest.mark.parametrize("song", cases([s for s in songs() if has_tempo_ramp(s)], "songs with a gradual tempo change"), ids=name)
def test_gradual_tempo_change_speeds_up_bit_by_bit(isolated_folder, song):
    """A gradual (linear) tempo change plays out over its section, the way Guitar Pro plays
    it — confirmed by ear, as Guitar Pro's own MIDI export writes such a change as one jump."""
    assert main([str(song), "-o", "ramped.mid"]) == 0
    (isolated_folder / "steps.toml").write_text("[midi]\ntempo_ramps = false\n")
    assert main([str(song), "-o", "stepped.mid", "--config", "steps.toml"]) == 0
    ramped, stepped = summary(isolated_folder / "ramped.mid"), summary(isolated_folder / "stepped.mid")
    # a ramp shows up as a long run of tempo changes all going the same way, up or down
    assert longest_run(ramped[2]) >= 8
    assert len(ramped[2]) > 3 * len(stepped[2])  # those steps are not in the stepped version
    assert abs(ramped[4] - stepped[4]) > 0.01  # speeding up or slowing down gradually takes
    # a different amount of time than holding the old tempo until the change


def longest_run(tempo: list[tuple[int, int]]) -> int:
    """How many tempo changes in a row keep moving in the same direction."""
    best = longest = 1
    direction = 0
    for (_, before), (_, after) in zip(tempo, tempo[1:], strict=False):
        step = (after > before) - (after < before)
        longest, direction = (longest + 1, step) if step and step == direction else (2 if step else 1, step)
        best = max(best, longest)
    return best
