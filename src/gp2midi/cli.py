from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter
from pathlib import Path

from . import config as settings
from . import gpif, midi, playback
from .config import Config, ConfigError
from .gpif import Articulation, Grace, Score, Track
from .notemap import normalize, qualified_name
from .velocity import DYNAMICS, marking

MARKING_LABELS = {"normal": "normal", "accent": "accent", "heavy_accent": "heavy accent", "ghost": "ghost"}


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _input_files(patterns: list[str]) -> list[Path]:
    """Files named directly, every .gp file in a folder, or the matches of a pattern such as
    "tabs/*.gp" or "**/*.gp" (Windows shells leave those to the program)."""
    files: list[Path] = []
    for pattern in patterns:
        path = Path(pattern)
        if path.is_dir():
            matches = sorted(path.glob("*.gp"))
            if not matches:
                raise SystemExit(f"error: no .gp files in {path}")
        elif path.exists():
            matches = [path]
        elif any(c in pattern for c in "*?["):
            matches = sorted(p for p in map(Path, glob.glob(pattern, recursive=True)) if p.is_file())
            if not matches:
                raise SystemExit(f"error: no files match {pattern}")
        else:
            raise SystemExit(f"error: {path} does not exist")
        files.extend(matches)
    unique, seen = [], set()
    for f in files:
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _settings(args: argparse.Namespace) -> Config:
    try:
        config = settings.find(args.config)
    except ConfigError as e:
        raise SystemExit(f"error: {e}") from None
    if config.source:
        print(f"settings: {config.source}", file=sys.stderr)
    if getattr(args, "track", None):
        config.tracks = args.track
    if getattr(args, "no_repeats", False):
        config.repeats = False
    return config


def select_tracks(score: Score, selectors: list[str | int] | None) -> tuple[list[Track], list[str]]:
    """The drum tracks to export, and why any selector did not yield one."""
    if selectors is None:
        return score.drum_tracks, []
    selected: list[Track] = []
    problems = []
    for selector in selectors:
        matches = _find_tracks(score, selector)
        if not matches:
            problems.append(f"no track {selector}" if isinstance(selector, int) else f'no track named "{selector}"')
        for track in matches:
            if not track.is_drumkit:
                problems.append(f"track {track.number} ({track.name}) is not a drum track")
            elif track not in selected:
                selected.append(track)
    return selected, problems


def _find_tracks(score: Score, selector: str | int) -> list[Track]:
    if isinstance(selector, int):
        return [t for t in score.tracks if t.number == selector]
    by_name = [t for t in score.tracks if t.name.strip().casefold() == selector.strip().casefold()]
    if by_name or not selector.strip().isdigit():
        return by_name
    return [t for t in score.tracks if t.number == int(selector)]


def _tracks_to_export(path: Path, score: Score, config: Config) -> list[Track]:
    tracks, problems = select_tracks(score, config.tracks)
    for problem in problems:
        _warn(f"{path.name}: {problem}")
    if not tracks:
        drums = ", ".join(f"{t.number} ({t.name})" for t in score.drum_tracks) or "none"
        _error(f"{path.name}: no drum track to export (drum tracks in this file: {drums})")
    return tracks


def _output_paths(files: list[Path], config: Config, override: str | None) -> list[Path]:
    if override and Path(override).suffix.lower() in (".mid", ".midi"):
        if len(files) > 1:
            raise SystemExit("error: -o must be a folder when exporting several files")
        paths = [Path(override)]
    elif override:
        paths = [Path(override) / f"{f.stem}.mid" for f in files]
    else:
        paths = [Path(config.output.format(name=f.stem, folder=os.path.abspath(f.parent))) for f in files]
    seen: dict[str, Path] = {}
    for source, target in zip(files, paths, strict=True):
        other = seen.setdefault(os.path.normcase(os.path.abspath(target)), source)
        if other != source:
            raise SystemExit(f"error: {other.name} and {source.name} would both be written to {target}")
    return paths


def _warn_unknown_notes(path: Path, config: Config, tracks: list[Track], reported: set[str]) -> None:
    for key in config.notes.unknown(a for t in tracks for a in t.articulations):
        if key not in reported:
            reported.add(key)
            _warn(f'{path.name}: [notes] "{key}" is not a drum articulation in this file')


def _warn_unsupported(path: Path, score: Score, tracks: list[Track]) -> None:
    for mb in score.master_bars:
        if mb.has_directions:
            _warn(f"{path.name}: bar {mb.index + 1} has a direction (D.S./D.C./coda), which is not followed yet")
        for t in tracks:
            if t.bar(mb).simile:
                _warn(f"{path.name}: bar {mb.index + 1} of {t.name!r} uses a simile mark, which is not expanded yet")


def cmd_export(args: argparse.Namespace) -> int:
    config = _settings(args)
    files = _input_files(args.inputs)
    targets = _output_paths(files, config, args.output)
    reported: set[str] = set()
    status = 0
    for path, target in zip(files, targets, strict=True):
        try:
            score = gpif.load(path)
            bars = playback.played_bars(score, expand_repeats=config.repeats)
        except gpif.GPFormatError as e:
            _error(str(e))
            status = 1
            continue
        tracks = _tracks_to_export(path, score, config)
        if not tracks:
            status = 1
            continue
        _warn_unknown_notes(path, config, tracks, reported)
        _warn_unsupported(path, score, tracks)
        parts = [
            midi.DrumPart(t.name, playback.drum_events(t, bars, config.velocity, config.notes, config.note_length))
            for t in tracks
        ]
        song = midi.build_midi(
            score.title,
            bars,
            playback.tempo_map(bars, ramps=config.tempo_ramps),
            parts,
            channel=config.channel - 1,
            ticks_per_quarter=config.ticks_per_quarter,
            markers=config.markers,
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            midi.save(song, target)
        except OSError as e:
            _error(f"cannot write {target}: {e.strerror}")
            status = 1
            continue
        notes = sum(len(p.events) for p in parts)
        print(f"{path.name} -> {target}  ({', '.join(t.name for t in tracks)}; {len(bars)} bars, {notes} notes)")
    return status


def _inspect_track(track: Track, bars: list[playback.PlayedBar], config: Config) -> None:
    hits: Counter[int] = Counter()
    velocities: Counter[tuple[str, str, int]] = Counter()
    for pb in bars:
        for voice in track.bar(pb.master_bar).voices:
            for beat in voice:
                is_grace = beat.grace is not Grace.NONE
                for note in beat.notes:
                    if note.tie_destination or not 0 <= note.articulation < len(track.articulations):
                        continue
                    hits[note.articulation] += 1
                    if config.notes.note(track.articulations[note.articulation]) is None:
                        continue
                    label = MARKING_LABELS[marking(note)] + (" + grace" if is_grace else "")
                    velocity = config.velocity.velocity(beat.dynamic, note, is_grace)
                    velocities[beat.dynamic, label, velocity] += 1

    print(f"\n   {track.name}: notes per articulation, as played")
    print(f"     {'count':>6}  {'articulation':28}  MIDI note")
    for index, count in hits.most_common():
        art = track.articulations[index]
        note = config.notes.note(art)
        shown = "left out" if note is None else str(note)
        if note != art.output_midi:
            shown += f"  (Guitar Pro: {art.output_midi})"
        print(f"     {count:6}  {art.name:28}  {shown}")

    source = "dynamic, accents and ghost notes" if config.velocity.markings else "dynamic only, as Guitar Pro"
    print(f"\n   {track.name}: velocity per dynamic and marking ({source})")
    print(f"     {'dynamic':7}  {'marking':22}  {'velocity':>8}  {'count':>6}")
    def order(item: tuple[tuple[str, str, int], int]) -> tuple[int, int, str]:
        (dynamic, label, velocity), _ = item
        return (DYNAMICS.index(dynamic) if dynamic in DYNAMICS else len(DYNAMICS), velocity, label)

    for (dynamic, label, velocity), count in sorted(velocities.items(), key=order):
        print(f"     {dynamic.lower():7}  {label:22}  {velocity:8}  {count:6}")


def cmd_inspect(args: argparse.Namespace) -> int:
    config = _settings(args)
    status = 0
    for path in _input_files(args.inputs):
        try:
            score = gpif.load(path)
            bars = playback.played_bars(score, expand_repeats=config.repeats)
        except gpif.GPFormatError as e:
            _error(str(e))
            status = 1
            continue
        tracks, problems = select_tracks(score, config.tracks)
        print(f"== {path.name}: {score.title!r} by {score.artist!r}, {len(score.master_bars)} bars ({len(bars)} as played)")
        for t in score.tracks:
            print(f"   {t.number:2}. {t.name} ({t.instrument_type}){'  <- export' if t in tracks else ''}")
        for problem in problems:
            _warn(f"{path.name}: {problem}")
        for track in tracks:
            _inspect_track(track, bars, config)
        print()
    return status


def cmd_config(args: argparse.Namespace) -> int:
    config = _settings(args)
    used: dict[str, tuple[int, Articulation]] = {}  # in drum kit order across all files
    for path in _input_files(args.inputs) if args.inputs else []:
        try:
            score = gpif.load(path)
        except gpif.GPFormatError as e:
            raise SystemExit(f"error: {e}") from None
        for track in select_tracks(score, config.tracks)[0]:
            indices = {n.articulation for mb in score.master_bars for v in track.bar(mb).voices for b in v for n in b.notes}
            for index in sorted(i for i in indices if 0 <= i < len(track.articulations)):
                art = track.articulations[index]
                used.setdefault(normalize(qualified_name(art)), (index, art))
    articulations = [art for _, art in sorted(used.values(), key=lambda item: item[0])]
    text = settings.to_toml(config, articulations)
    if not args.output:
        sys.stdout.write(text)
        return 0
    target = Path(args.output)
    if target.exists() and not args.force:
        raise SystemExit(f"error: {target} already exists; add --force to overwrite it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gp2midi",
        description="Export drum tracks from Guitar Pro 7/8 files to General MIDI, keeping dynamics, "
        'accents and ghost notes as note velocities. "export" may be left out: '
        '`gp2midi "tabs/*.gp" -o midi` exports.',
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def inputs(
        p: argparse.ArgumentParser,
        nargs: str = "+",
        help: str = '.gp files, folders containing them, or patterns such as "tabs/*.gp" or "**/*.gp"',
    ) -> None:
        p.add_argument("inputs", nargs=nargs, metavar="INPUT", help=help)

    def track(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--track", action="append", metavar="NAME|NUMBER",
            help="drum track to export, by name or number as listed by inspect; repeat for more "
            "(default: the tracks setting, normally every drum track)",
        )

    def config(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--config", metavar="FILE",
            help=f"settings file (default: {settings.FILENAME} in the current folder, if there is one)",
        )

    p = sub.add_parser("export", help="write drum tracks to MIDI files")
    inputs(p)
    p.add_argument("-o", "--output", help="output folder, or a .mid file for a single input (default: the [output] path setting)")
    track(p)
    p.add_argument("--no-repeats", action="store_true", help="play every bar once, ignoring repeats")
    config(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("inspect", help="list tracks, and the MIDI notes and velocities drum notes get")
    inputs(p)
    track(p)
    config(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("config", help="show the settings in use, as a gp2midi.toml to start from")
    inputs(p, "*", "also list the drum articulations these .gp files use, under [notes]")
    p.add_argument("-o", "--output", metavar="FILE", help=f"write to FILE, e.g. {settings.FILENAME}, instead of printing")
    p.add_argument("--force", action="store_true", help="overwrite FILE if it exists")
    config(p)
    p.set_defaults(func=cmd_config)

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in sub.choices and not argv[0].startswith("-"):
        argv.insert(0, "export")  # files straight after the program name: export them
    args = parser.parse_args(argv)
    return args.func(args)
