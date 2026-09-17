from fractions import Fraction

import mido
import pytest

from gp2midi import gpif, playback
from gp2midi.cli import main
from gp2midi.gpif import Accent
from gp2midi.midi import DrumPart, build_midi, microseconds_per_quarter
from gp2midi.notemap import NoteMap
from gp2midi.velocity import DYNAMICS, GUITAR_PRO, VelocityMap, spread

from gpif_builder import CRASH_CHOKE, KICK, MB, RIMSHOT, SNARE, B, N, build, write_gp

HEAVY, ACCENT, STACCATO = 0x04, 0x08, 0x01


def events(master_bars, velocities=None, **kwargs):
    score = gpif.parse_gpif(build(master_bars))
    bars = playback.played_bars(score)
    return playback.drum_events(score.drum_tracks[0], bars, velocities or VelocityMap(), **kwargs)


def note(accent=Accent.NONE, ghost=False):
    return gpif.Note(0, None, accent, ghost, False)


def test_parse_tracks_and_articulations():
    score = gpif.parse_gpif(build([MB([[B(notes=[N(RIMSHOT)])]])]))
    assert [(t.number, t.is_drumkit) for t in score.tracks] == [(1, False), (2, True)]
    drums = score.drum_tracks[0]
    assert drums.articulations[RIMSHOT].output_midi == 38
    assert drums.bar(score.master_bars[0]).voices[0][0].notes[0].articulation == RIMSHOT


@pytest.mark.parametrize(
    "beat, quarters",
    [
        (B("Quarter"), 1),
        (B("16th"), Fraction(1, 4)),
        (B("Eighth", dots=1), Fraction(3, 4)),
        (B("Half", dots=2), Fraction(7, 2)),
        (B("Eighth", tuplet=(3, 2)), Fraction(1, 3)),
        (B("32nd", tuplet=(12, 8)), Fraction(1, 12)),
    ],
)
def test_rhythm_durations(beat, quarters):
    score = gpif.parse_gpif(build([MB([[beat]])]))
    assert score.drum_tracks[0].bar(score.master_bars[0]).voices[0][0].duration == quarters


def test_accent_flags():
    score = gpif.parse_gpif(build([MB([[B(notes=[N(SNARE, HEAVY), N(KICK, ACCENT), N(CRASH_CHOKE, STACCATO, ghost=True)])]])]))
    notes = score.drum_tracks[0].bar(score.master_bars[0]).voices[0][0].notes
    assert [n.accent for n in notes] == [Accent.HEAVY, Accent.ACCENT, Accent.NONE]
    assert [n.ghost for n in notes] == [False, False, True]


def test_velocity_follows_dynamics_and_markings():
    bar = MB([[
        B(notes=[N(SNARE)], dynamic="F"),
        B(notes=[N(SNARE, ACCENT)], dynamic="F"),
        B(notes=[N(SNARE, HEAVY)], dynamic="F"),
        B(notes=[N(SNARE, ghost=True)], dynamic="F"),
    ]])
    assert [e.velocity for e in events([bar])] == [83, 95, 107, 45]


def test_default_velocities_are_distinct_and_in_range():
    vm = VelocityMap()
    for dynamic in DYNAMICS:
        per_marking = [vm.velocity(dynamic, note(a)) for a in Accent] + [vm.velocity(dynamic, note(ghost=True))]
        assert all(1 <= v <= 127 for v in per_marking)
        assert len(set(per_marking)) == 4  # nothing collapses onto the ceiling
    assert vm.velocity("FFF", note(Accent.HEAVY)) == 127


def test_ghost_note_that_is_also_accented_counts_as_ghost():
    vm = VelocityMap()
    assert vm.velocity("F", note(Accent.HEAVY, ghost=True)) == vm.velocity("F", note(ghost=True))


def test_grace_offset_is_clamped():
    vm = VelocityMap()
    vm.table["ghost"] = spread(10, 10)
    assert vm.velocity("PPP", note(ghost=True), is_grace=True) == 1


def test_markings_off_gives_guitar_pro_velocities():
    vm = VelocityMap(markings=False)
    for i, dynamic in enumerate(DYNAMICS):
        for n in (note(), note(Accent.HEAVY), note(ghost=True)):
            assert vm.velocity(dynamic, n) == vm.velocity(dynamic, n, is_grace=True) == GUITAR_PRO[i]
    # measured from Guitar Pro's own exports; only ppp does not appear in them
    assert [vm.velocity(d, note()) for d in DYNAMICS[1:]] == [38, 51, 64, 76, 89, 102, 114]


def test_spread():
    assert spread(20, 55) == (20, 25, 30, 35, 40, 45, 50, 55)
    assert spread(1, 127)[0] == 1 and spread(1, 127)[-1] == 127


def order(*bars):
    score = gpif.parse_gpif(build(list(bars)))
    return playback.playback_order(score.master_bars)


def test_repeat():
    assert order(MB(), MB(repeat=(True, False, 0)), MB(repeat=(False, True, 3)), MB()) == [0, 1, 2, 1, 2, 1, 2, 3]


def test_repeat_without_start_goes_back_to_beginning():
    assert order(MB(), MB(repeat=(False, True, 2)), MB()) == [0, 1, 0, 1, 2]


def test_alternate_endings_spanning_two_bars():
    # a pattern that turns up in real scores: two bars of first ending, one of second ending
    bars = [MB(repeat=(True, False, 0)), MB(endings="1"), MB(endings="1", repeat=(False, True, 2)), MB(endings="2"), MB()]
    assert order(*bars) == [0, 1, 2, 0, 3, 4]


def test_three_alternate_endings():
    bars = [MB(repeat=(True, False, 0)), MB(endings="1 2", repeat=(False, True, 3)), MB(endings="3"), MB()]
    assert order(*bars) == [0, 1, 0, 1, 0, 2, 3]


def test_consecutive_repeats_with_endings():
    bars = [
        MB(repeat=(True, False, 0)), MB(endings="1", repeat=(False, True, 2)), MB(endings="2"),
        MB(repeat=(True, False, 0)), MB(repeat=(False, True, 2)),
    ]
    assert order(*bars) == [0, 1, 0, 2, 3, 4, 3, 4]


def test_tempo_constant_and_linear_ramp():
    score = gpif.parse_gpif(build([MB(), MB(), MB()], tempo=[(0, 0, 100, False), (1, 0, 100, True), (2, 0, 60, False)]))
    tempo = playback.tempo_map(playback.played_bars(score), step=Fraction(1))
    # ramp from 100 at bar 2 to 60 at bar 3, one change per quarter note at the step's midpoint
    assert tempo == [(0, 100), (4, 95.0), (5, 85.0), (6, 75.0), (7, 65.0), (8, 60)]


def test_tempo_ramps_can_be_turned_off():
    score = gpif.parse_gpif(build([MB(), MB(), MB()], tempo=[(0, 0, 100, False), (1, 0, 100, True), (2, 0, 60, False)]))
    assert playback.tempo_map(playback.played_bars(score), ramps=False) == [(0, 100), (8, 60)]


def test_tempo_reference_unit():
    gp = build([MB()], tempo=[(0, 0, 60, False)]).replace(b"60 2", b"60 3")  # dotted quarter
    score = gpif.parse_gpif(gp)
    assert playback.tempo_map(playback.played_bars(score)) == [(0, 90)]


def test_tempo_follows_repeats():
    bars = [MB(repeat=(True, False, 0)), MB(repeat=(False, True, 2)), MB()]
    score = gpif.parse_gpif(build(bars, tempo=[(0, 0, 100, False), (1, 0.5, 50, False)]))
    tempo = playback.tempo_map(playback.played_bars(score))
    assert tempo == [(0, 100), (6, 50), (8, 100), (14, 50)]


def test_tempo_is_truncated_like_guitar_pro():
    assert microseconds_per_quarter(106) == 566037  # 566037.7...


def test_grace_before_beat_steals_from_previous_beat():
    bar = MB([[
        B(notes=[N(KICK)]),
        B("64th", notes=[N(SNARE)], grace="BeforeBeat", dynamic="F"),
        B(notes=[N(SNARE)], dynamic="F"),
    ]])
    e = events([bar])
    assert [(x.start, x.key, x.velocity) for x in e] == [(0, 36, 73), (Fraction(15, 16), 38, 63), (1, 38, 83)]
    assert e[0].end == Fraction(15, 16)  # the beat before ends where the grace note starts
    assert e[1].end == 1  # the grace note ends at the main stroke


def test_grace_before_beat_steals_across_a_bar_line():
    bars = [MB([[B("Whole", notes=[N(KICK)])]]), MB([[B("64th", notes=[N(SNARE)], grace="BeforeBeat"), B(notes=[N(SNARE)])]])]
    e = events(bars)
    assert [(x.start, x.end, x.key) for x in e] == [
        (0, Fraction(63, 16), 36), (Fraction(63, 16), 4, 38), (4, 5, 38),
    ]


def test_grace_on_beat_delays_and_shortens_the_main_note():
    bar = MB([[B("32nd", notes=[N(SNARE)], grace="OnBeat"), B(notes=[N(SNARE)]), B(notes=[N(KICK)])]])
    e = events([bar])
    assert [(x.start, x.end, x.key) for x in e] == [(0, Fraction(1, 8), 38), (Fraction(1, 8), 1, 38), (1, 2, 36)]


def test_staccato_note_is_half_as_long():
    bar = MB([[B("Half", notes=[N(SNARE, STACCATO), N(KICK)])]])
    assert [(x.start, x.end, x.key) for x in events([bar])] == [(0, 2, 36), (0, 1, 38)]


def test_tie_extends_instead_of_retriggering():
    bar = MB([[B(notes=[N(CRASH_CHOKE)]), B(notes=[N(CRASH_CHOKE, tie=True)]), B(notes=[N(KICK)])]])
    e = events([bar])
    assert [(x.start, x.end, x.key) for x in e] == [(0, 2, 57), (2, 3, 36)]


def test_voices_overlapping_same_drum_keep_loudest():
    bar = MB([[B("Half", notes=[N(KICK)])], [B(notes=[N(KICK, HEAVY)]), B(notes=[N(KICK)])]])
    e = events([bar])
    assert [(x.start, x.end, x.velocity) for x in e] == [(0, 1, 97), (1, 2, 73)]


def test_note_map_and_fixed_note_length():
    bar = MB([[B("Half", notes=[N(RIMSHOT)]), B(notes=[N(CRASH_CHOKE)]), B(notes=[N(CRASH_CHOKE, tie=True), N(KICK)])]])
    notes = NoteMap({"Snare (rim shot)": 40, "Kick (hit)": None})
    e = events([bar], notes=notes, note_length=Fraction(1, 8))
    # kick left out; the tied crash is neither struck again nor lengthened
    assert [(x.start, x.end, x.key) for x in e] == [(0, Fraction(1, 8), 40), (2, Fraction(17, 8), 57)]


def test_short_notes_never_get_zero_length_at_coarse_resolution():
    score = gpif.parse_gpif(build([MB([[B("64th", notes=[N(SNARE)])]])]))
    bars = playback.played_bars(score)
    ev = playback.drum_events(score.drum_tracks[0], bars, VelocityMap())
    midi = build_midi("t", bars, [], [DrumPart("d", ev)], ticks_per_quarter=4)
    now, timeline = 0, []
    for m in midi.tracks[1]:
        now += m.time
        if m.type.startswith("note"):
            timeline.append((now, m.type))
    assert timeline == [(0, "note_on"), (1, "note_off")]


def test_export_cli_writes_midi(tmp_path, capsys):
    bars = [
        MB([[B(notes=[N(KICK)], dynamic="FF"), B(notes=[N(SNARE, HEAVY)], dynamic="FF")]], time="2/4", section="Verse Ω"),
        MB([[B("Half", notes=[N(SNARE, ghost=True)], dynamic="FF")]], time="2/4"),
    ]
    gp = write_gp(tmp_path / "song.gp", build(bars, tempo=[(0, 0, 90, False)], title="Tête-à-tête"))
    assert main(["export", str(gp), "-o", str(tmp_path / "out")]) == 0
    mf = mido.MidiFile(tmp_path / "out" / "song.mid", charset="utf-8")
    assert mf.ticks_per_beat == 960
    conductor = [m for m in mf.tracks[0] if m.is_meta]
    assert conductor[0].type == "track_name" and conductor[0].name == "Tête-à-tête"
    assert any(m.type == "set_tempo" and round(mido.tempo2bpm(m.tempo)) == 90 for m in conductor)
    assert any(m.type == "time_signature" and (m.numerator, m.denominator) == (2, 4) for m in conductor)
    assert any(m.type == "marker" and m.text == "Verse Ω" for m in conductor)
    now, notes = 0, []
    for m in mf.tracks[1]:
        now += m.time
        if m.type == "note_on":
            notes.append((now, m.channel, m.note, m.velocity))
    assert notes == [(0, 9, 36, 93), (960, 9, 38, 117), (1920, 9, 38, 50)]
