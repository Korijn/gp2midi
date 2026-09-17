import codecs
import tomllib
from fractions import Fraction
from pathlib import Path

import mido
import pytest

from gp2midi import config as settings
from gp2midi.cli import main
from gp2midi.config import Config, ConfigError
from gp2midi.gpif import Articulation
from gp2midi.notemap import NoteMap
from gp2midi.velocity import VelocityMap, spread

from gpif_builder import ACOUSTIC_KICK, KICK, MB, RIMSHOT, SNARE, B, N, build, write_gp

FULL = """
tracks = ["Drums", 2]
repeats = false

[output]
path = "midi/{name} drums.mid"

[midi]
channel = 1
ticks_per_quarter = 480
note_length = "1/32"
markers = false

[velocity]
markings = true
normal = [40, 110]
ghost = [10, 12, 14, 16, 18, 20, 22, 24]
grace_offset = -5

[chokes]
mode = "aftertouch"
at = "1/16"
velocity = 90
pressure = 64

[chokes.notes]
"Crash medium (choke)" = 119

[notes]
"Snare (rim shot)" = 40
"kick   (HIT)" = false
"""


def parse(text: str) -> Config:
    return settings.parse(tomllib.loads(text))


def test_empty_settings_are_the_defaults():
    assert parse("") == Config()


def test_all_settings():
    c = parse(FULL)
    assert c.tracks == ["Drums", 2]
    assert c.repeats is False
    assert c.output == "midi/{name} drums.mid"
    assert (c.channel, c.ticks_per_quarter, c.note_length, c.markers) == (1, 480, Fraction(1, 8), False)
    assert c.velocity.table["normal"] == spread(40, 110)
    assert c.velocity.table["accent"] == VelocityMap().table["accent"]  # not set: default
    assert c.velocity.table["ghost"] == (10, 12, 14, 16, 18, 20, 22, 24)
    assert c.velocity.grace_offset == -5
    assert (c.chokes.mode, c.chokes.at, c.chokes.velocity, c.chokes.pressure) == ("aftertouch", Fraction(1, 4), 90, 64)
    assert c.chokes.notes.entries == {"Crash medium (choke)": 119}
    assert c.notes.entries == {"Snare (rim shot)": 40, "kick   (HIT)": None}


@pytest.mark.parametrize(
    "text, message",
    [
        ("trakcs = 'auto'", "unknown setting: trakcs"),
        ("[dynamics]\nf = 80", "unknown setting: dynamics"),
        ("[midi]\nchanel = 10", "unknown setting in [midi]: chanel"),
        ("tracks = []", "tracks: expected"),
        ("tracks = [0]", "tracks: expected"),
        ("repeats = 'yes'", "repeats: expected true or false"),
        ("[output]\npath = '{song}.mid'", "only {name} and {folder}"),
        ("[midi]\nchannel = 0", "[midi] channel: expected a whole number from 1 to 16, got 0"),
        ("[midi]\nnote_length = '1/0'", "note_length: expected"),
        ("[midi]\nnote_length = 0.125", "note_length: expected"),
        ("[velocity]\nghost = [10]", "[velocity] ghost: expected [ppp, fff] or eight velocities"),
        ("[velocity]\nnormal = [0, 100]", "[velocity] normal: expected"),
        ("[velocity]\nmarkings = 1", "[velocity] markings: expected true or false"),
        ("[chokes]\nmode = 'poly'", '[chokes] mode: expected "note", "aftertouch" or "off"'),
        ("[chokes]\nat = 'later'", '[chokes] at: expected "end" or a note value'),
        ("[chokes]\nvelocity = 0", "[chokes] velocity: expected a whole number from 1 to 127"),
        ("[chokes]\nmoed = 'off'", "unknown setting in [chokes]: moed"),
        ("[chokes.notes]\n'China (choke)' = 200", "expected a MIDI note number"),
        ("[notes]\n'Snare (hit)' = 128", 'expected a MIDI note number'),
        ("[notes]\n'Snare (hit)' = true", 'expected a MIDI note number'),
        ("[notes]\nSnare.hit = 38", "put articulation names in quotes"),
        ("[notes]\n'Snare (hit)' = 38\n'snare (hit)' = 40", 'same articulation as "Snare (hit)"'),
    ],
)
def test_invalid_settings_are_reported(text, message):
    with pytest.raises(ConfigError) as e:
        parse(text)
    assert message in str(e.value)


def test_settings_file_round_trip():
    c = parse(FULL)
    c.velocity.markings = False
    assert parse(settings.to_toml(c)) == c
    assert parse(settings.to_toml(Config())) == Config()


def test_settings_file_lists_used_articulations():
    hit = Articulation("Snare", "Snare (hit)", 38, 38)
    rim = Articulation("Snare", "Snare (rim shot)", 91, 38)
    text = settings.to_toml(parse(FULL), [hit, rim])
    assert '"Snare (hit)" = 38\n"Snare (rim shot)" = 40\n"kick   (HIT)" = false\n' in text


def test_settings_file_lists_choke_notes():
    crash = Articulation("Crash Medium", "Crash medium (choke)", 98, 57)
    text = settings.to_toml(Config(), [crash])
    chokes = text.split("[chokes.notes]")[1]
    assert '"Crash medium (choke)" = 98' in chokes  # the note that chokes it
    assert '"Crash medium (choke)" = 57' in text.split("[notes]")[-1]  # the cymbal itself


@pytest.mark.parametrize("encoding, bom",[("utf-8", b""), ("utf-8", codecs.BOM_UTF8), ("utf-16-le", codecs.BOM_UTF16_LE)])
def test_settings_file_encodings(tmp_path, encoding, bom):
    path = tmp_path / "gp2midi.toml"
    path.write_bytes(bom + '[notes]\n"Crash medium (choke)" = 57 # touché\n'.encode(encoding))
    assert settings.load(path).notes.entries == {"Crash medium (choke)": 57}


def test_syntax_error_names_the_file(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("[midi\n")
    with pytest.raises(ConfigError, match="broken.toml"):
        settings.load(path)


def test_settings_file_is_found_in_current_folder(isolated_folder):
    assert settings.find() == Config() and settings.find().source is None
    (isolated_folder / "gp2midi.toml").write_text("[midi]\nchannel = 3\n")
    found = settings.find()
    assert found.channel == 3 and found.source == isolated_folder / "gp2midi.toml"
    (isolated_folder / "other.toml").write_text("[midi]\nchannel = 4\n")
    assert settings.find("other.toml").channel == 4


def test_note_map_lookup():
    kick = Articulation("Kick Drum", "Kick (hit)", 36, 36)
    acoustic = Articulation("Acoustic Kick Drum", "Kick (hit)", 35, 35)
    snare = Articulation("Snare", "Snare (hit)", 38, 38)
    notes = NoteMap({" kick (hit)": 30, "Acoustic Kick Drum / Kick (HIT)": 31, "Snare (hit)": None, "Snare (hti)": 1})
    assert notes.note(kick) == 30
    assert notes.note(acoustic) == 31  # instrument/articulation wins over the bare name
    assert notes.note(snare) is None
    assert notes.note(Articulation("Ride", "Ride (bell)", 53, 53)) == 53  # not listed: Guitar Pro's note
    assert notes.unknown([kick, acoustic, snare]) == ["Snare (hti)"]


def song(tmp_path: Path, name: str = "song.gp") -> Path:
    bars = [MB([[B(notes=[N(KICK)]), B(notes=[N(RIMSHOT)]), B(notes=[N(ACOUSTIC_KICK)]), B(notes=[N(SNARE, ghost=True)])]], section="A")]
    return write_gp(tmp_path / name, build(bars))


def midi_notes(path: Path) -> list[tuple[int, int, int, int]]:
    mf = mido.MidiFile(path)
    now, notes = 0, []
    for m in mido.merge_tracks(mf.tracks):
        now += m.time
        if m.type == "note_on" and m.velocity:
            notes.append((now, m.channel, m.note, m.velocity))
    return notes


def test_export_uses_settings_file_in_current_folder(isolated_folder):
    gp = song(isolated_folder)
    (isolated_folder / "gp2midi.toml").write_text(
        "[output]\npath = 'out/{name}-drums.mid'\n"
        "[midi]\nchannel = 2\nticks_per_quarter = 480\nmarkers = false\n"
        "[velocity]\nghost = [5, 40]\n"
        "[notes]\n'Snare (rim shot)' = 40\n'Acoustic Kick Drum/Kick (hit)' = false\n"
    )
    assert main(["export", str(gp)]) == 0
    out = isolated_folder / "out" / "song-drums.mid"
    assert mido.MidiFile(out).ticks_per_beat == 480
    assert not any(m.type == "marker" for m in mido.MidiFile(out).tracks[0])
    assert midi_notes(out) == [(0, 1, 36, 73), (480, 1, 40, 73), (1440, 1, 38, 25)]


def test_output_path_with_folder_placeholder(isolated_folder):
    (isolated_folder / "tabs").mkdir()
    gp = song(isolated_folder / "tabs")
    (isolated_folder / "gp2midi.toml").write_text("[output]\npath = '{folder}/MIDI/{name}.mid'\n")
    assert main(["export", str(gp)]) == 0
    assert (isolated_folder / "tabs" / "MIDI" / "song.mid").is_file()


def test_command_line_overrides_settings(isolated_folder):
    gp = song(isolated_folder)
    (isolated_folder / "gp2midi.toml").write_text("tracks = ['Guitar']\n[output]\npath = 'unused/{name}.mid'\n")
    assert main(["export", str(gp)]) == 1  # the guitar is not a drum track
    assert main(["export", str(gp), "--track", "2", "-o", "x.mid"]) == 0
    assert main(["export", str(gp), "--track", "drums", "-o", "folder"]) == 0
    assert (isolated_folder / "x.mid").is_file() and (isolated_folder / "folder" / "song.mid").is_file()
    assert not (isolated_folder / "unused").exists()


def test_glob_patterns_and_implicit_export(isolated_folder):
    (isolated_folder / "tabs" / "live").mkdir(parents=True)
    song(isolated_folder / "tabs", "a.gp")
    song(isolated_folder / "tabs" / "live", "b.gp")
    assert main(["tabs/*.gp", "-o", "one"]) == 0  # "export" may be left out
    assert [p.name for p in sorted((isolated_folder / "one").glob("*.mid"))] == ["a.mid"]
    assert main(["export", "**/*.gp", "-o", "all"]) == 0
    assert [p.name for p in sorted((isolated_folder / "all").glob("*.mid"))] == ["a.mid", "b.mid"]
    with pytest.raises(SystemExit, match="no files match"):
        main(["tabs/*.gp5"])
    with pytest.raises(SystemExit, match="does not exist"):
        main(["nowhere.gp"])


def test_two_inputs_writing_the_same_file_is_refused(isolated_folder):
    song(isolated_folder, "a.gp"), song(isolated_folder, "b.gp")
    (isolated_folder / "gp2midi.toml").write_text("[output]\npath = 'all.mid'\n")
    with pytest.raises(SystemExit, match="would both be written to all.mid"):
        main(["export", "a.gp", "b.gp"])


def test_invalid_settings_file_stops_the_export(isolated_folder):
    song(isolated_folder)
    (isolated_folder / "gp2midi.toml").write_text("[midi]\nchannel = 17\n")
    with pytest.raises(SystemExit, match=r"gp2midi.toml: \[midi\] channel"):
        main(["export", "song.gp"])


def test_config_command_writes_a_starting_file(isolated_folder, capsys):
    song(isolated_folder)
    assert main(["config", "song.gp", "-o", "gp2midi.toml"]) == 0
    text = (isolated_folder / "gp2midi.toml").read_text(encoding="utf-8")
    assert '"Kick Drum/Kick (hit)" = 36' in text and '"Acoustic Kick Drum/Kick (hit)" = 35' in text  # shared name
    assert '"Snare (rim shot)" = 38' in text
    with pytest.raises(SystemExit, match="already exists"):
        main(["config", "-o", "gp2midi.toml"])
    # the written file changes nothing about the export
    assert main(["export", "song.gp", "-o", "with.mid"]) == 0
    (isolated_folder / "empty.toml").write_text("")
    assert main(["export", "song.gp", "-o", "without.mid", "--config", "empty.toml"]) == 0
    assert midi_notes(isolated_folder / "with.mid") == midi_notes(isolated_folder / "without.mid")


def test_inspect_shows_mapping_and_velocities(isolated_folder, capsys):
    song(isolated_folder)
    (isolated_folder / "gp2midi.toml").write_text("[notes]\n'Snare (rim shot)' = 40\n'Kick Drum/Kick (hit)' = false\n")
    assert main(["inspect", "song.gp"]) == 0
    out = capsys.readouterr().out
    assert "2. Drums (drumKit)  <- export" in out
    assert "Snare (rim shot)              40  (Guitar Pro: 38)" in out
    assert "Kick (hit)                    left out  (Guitar Pro: 36)" in out
    assert "mf       ghost                         40" in out
