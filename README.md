# gp2midi

Exports drum tracks from Guitar Pro 8 files (`.gp`) to General MIDI, keeping the dynamics you
notated. Guitar Pro's own MIDI export follows the dynamic marking but throws away accents and
ghost notes, so a part written with ghost notes and accents comes out flat.

Only Guitar Pro 7/8 files (`.gp`) are read; `.gp3`, `.gp4` and `.gp5` are a different format
and not supported.

## Usage

```bash
uv run gp2midi "path/to/01 Tabs" -o midi/      # every .gp file in a folder
uv run gp2midi "tabs/*.gp" -o midi/            # a pattern; "**/*.gp" searches subfolders too
uv run gp2midi "01 Lethe.gp" -o lethe.mid      # one file
uv run gp2midi inspect "01 Lethe.gp"           # tracks, articulations, velocities per marking
uv run gp2midi config "01 Tabs" -o gp2midi.toml   # write a settings file to edit
```

`export` may be left out, as above. Quote patterns: Windows shells leave them to the program.
Options: `--track NAME|NUMBER` (repeatable), `--no-repeats`, `--config FILE`.

## Settings

Settings are optional: with no file, every drum track is exported with the velocities below
and Guitar Pro's General MIDI notes. gp2midi reads `gp2midi.toml` from the folder you run it
in, or the file you pass with `--config`. `gp2midi config` prints the settings in use, with a
line for every drum articulation your songs contain, ready to edit:

```toml
tracks = "auto"          # or ["Drumkit"], or [6]
repeats = true           # play repeats and alternate endings like Guitar Pro

[output]
path = "{name}.mid"      # {name}: the .gp file's name, {folder}: the folder it is in

[midi]
channel = 10
ticks_per_quarter = 960
note_length = "written"  # or one length for every note, e.g. "1/32"
markers = true           # section names (Intro, Verse, ...) as MIDI markers
tempo_ramps = true       # false: gradual tempo changes jump, as Guitar Pro exports them

[velocity]
markings = true          # false: dynamics only, with Guitar Pro's velocities (see below)
normal = [33, 103]       # [ppp, fff], spread over the eight dynamics, or all eight values
accent = [45, 115]
heavy_accent = [57, 127]
ghost = [20, 55]
grace_offset = -20       # flams and drags, relative to their own marking

[notes]
"Snare (rim shot)" = 40  # unlisted articulations keep Guitar Pro's General MIDI note
"Hi-Hat (half)" = 23     # false leaves an articulation out
```

So a note at **f** is 83, with an accent 95, with a heavy accent 107, and as a ghost note 45.
The ranges leave headroom: fff with a heavy accent lands exactly on 127, and no two markings
collapse onto the ceiling. Ghost notes have a narrow range on purpose — a ghost note should
stay a ghost note in a loud passage. A note that is both ghosted and accented counts as a
ghost note.

## Matching Guitar Pro's own export

With `markings = false`, `tempo_ramps = false` and `ticks_per_quarter = 480`, gp2midi produces
exactly what Guitar Pro exports: same note positions, lengths, note numbers and velocities.
That is checked against Guitar Pro's exports of three songs in `tests/test_guitar_pro.py`, and
it is how the details below were established.

- Guitar Pro's velocities are tenths of 127 per dynamic: ppp 25, pp 38, p 51, mp 64, mf 76,
  f 89, ff 102, fff 114 (every value but ppp measured).
- Accents, heavy accents and ghost notes do not change velocity in its export.
- A grace note (flam) is played before the beat for the length it is written as, and the note
  before it is shortened to make room. A staccato note is half as long.
- Gradual (linear) tempo changes are exported as one jump. gp2midi plays them out gradually by
  default, with a tempo change every 32nd note.

## What the MIDI contains

- Type 1 file, drums on channel 10, using each articulation's General MIDI note (rim shot →
  38, crash choke → 57) unless `[notes]` says otherwise.
- Repeats and alternate endings played out, tempo and time signature changes, section names as
  markers. Tied notes are not struck again.
- Text is written as UTF-8, like Guitar Pro does.

Not handled yet, with a warning when a file uses them: D.S./D.C./coda directions and simile
(repeat bar) marks. Hairpins (crescendo/diminuendo) do not change velocity.

## Development

```bash
uv run pytest
```
