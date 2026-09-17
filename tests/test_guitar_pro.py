"""Checks against the band's real Guitar Pro files, and against Guitar Pro's own MIDI
exports of some of them. Skipped where those files are not available."""

from collections import defaultdict
from pathlib import Path

import mido
import pytest

from gp2midi.cli import main

TABS = Path(r"G:\.shortcut-targets-by-id\0BxjRWu5LHczWdkMtajJjWXl0ZTQ\Façade\Façade III - Silk and Gold\01 Tabs")
EXPORTS = TABS / "GP Exports"
# songs Guitar Pro exported itself; the drums are the last thing playing in the ones marked
# True, so there the whole file must be as long as ours (which holds drums only)
NATIVE = {"01 Lethe": True, "06 Silk and Gold": True, "07 The Great Nothing": False}


def available(song: str) -> bool:
    return (TABS / f"{song}.gp").is_file() and (EXPORTS / f"{song}.mid").is_file()


@pytest.mark.skipif(not TABS.is_dir(), reason="real Guitar Pro files not available")
def test_real_files_export(tmp_path):
    assert main(["export", str(TABS), "-o", str(tmp_path)]) == 0
    for path in TABS.glob("*.gp"):
        mf = mido.MidiFile(tmp_path / f"{path.stem}.mid")
        velocities = {m.velocity for m in mf.tracks[1] if m.type == "note_on"}
        assert len(velocities) > 3


def summary(path: Path):
    """Drum notes as (start tick, end tick, note, velocity), tempo changes (repeats of the
    same tempo dropped: Guitar Pro restates the tempo after a repeat) and time signatures."""
    mf = mido.MidiFile(path)
    notes, tempo, meter = [], [], []
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
    return mf.ticks_per_beat, sorted(notes), tempo, meter, round(mf.length, 6)


@pytest.mark.parametrize("song", NATIVE)
def test_identical_to_guitar_pro_export(isolated_folder, song):
    if not available(song):
        pytest.skip(f"Guitar Pro's own export of {song} not available")
    (isolated_folder / "gp.toml").write_text(
        "[midi]\nticks_per_quarter = 480\ntempo_ramps = false\n[velocity]\nmarkings = false\n"
    )
    ours = isolated_folder / "ours.mid"
    assert main([str(TABS / f"{song}.gp"), "-o", str(ours), "--config", "gp.toml"]) == 0
    tpq, notes, tempo, meter, length = summary(ours)
    native_tpq, native_notes, native_tempo, native_meter, native_length = summary(EXPORTS / f"{song}.mid")
    assert notes == native_notes
    assert (tempo, meter) == (native_tempo, native_meter)
    if NATIVE[song]:
        assert length == native_length


@pytest.mark.skipif(not available("06 Silk and Gold"), reason="Guitar Pro's own export not available")
def test_only_velocities_differ_from_guitar_pro_export_by_default(isolated_folder):
    ours = isolated_folder / "ours.mid"
    assert main(["export", str(TABS / "06 Silk and Gold.gp"), "-o", str(ours)]) == 0
    tpq, notes, tempo, meter, length = summary(ours)
    native_tpq, native_notes, native_tempo, native_meter, native_length = summary(EXPORTS / "06 Silk and Gold.mid")
    scale = tpq // native_tpq
    assert [(s // scale, e // scale, k) for s, e, k, _ in notes] == [(s, e, k) for s, e, k, _ in native_notes]
    assert [(t // scale, v) for t, v in tempo] == native_tempo
    assert [(t // scale, n, d) for t, n, d in meter] == native_meter
    assert length == native_length
    assert len({v for *_, v in notes}) > len({v for *_, v in native_notes})
