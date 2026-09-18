"""Builds minimal score.gpif documents for tests."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# (element, articulation, input midi, output midi)
KIT = [
    ("Snare", "Snare (hit)", 38, 38),
    ("Snare", "Snare (rim shot)", 91, 38),
    ("Kick Drum", "Kick (hit)", 36, 36),
    ("Crash Medium", "Crash medium (choke)", 98, 57),
    ("Acoustic Kick Drum", "Kick (hit)", 35, 35),
    ("Tom Low", "Low Tom (hit)", 45, 45),
    ("Tom Very Low", "Very Low Tom (hit)", 43, 43),
    ("Charley", "Pedal Hi-Hat (hit)", 44, 44),
]
SNARE, RIMSHOT, KICK, CRASH_CHOKE, ACOUSTIC_KICK, LOW_TOM, VERY_LOW_TOM, PEDAL_HIHAT = range(len(KIT))


@dataclass
class N:
    articulation: int
    accent: int = 0
    ghost: bool = False
    tie: bool = False


@dataclass
class B:
    value: str = "Quarter"
    notes: list[N] = field(default_factory=list)
    dynamic: str = "MF"
    grace: str | None = None
    tuplet: tuple[int, int] | None = None
    dots: int = 0


@dataclass
class MB:
    voices: list[list[B]] = field(default_factory=list)
    time: str = "4/4"
    repeat: tuple[bool, bool, int] | None = None  # start, end, count
    endings: str | None = None
    section: str | None = None


def build(master_bars: list[MB], tempo: list[tuple[int, float, float, bool]] = (), title: str = "Test") -> bytes:
    """tempo: (bar, position, bpm, linear)"""
    out = [f"<GPIF><Score><Title><![CDATA[{title}]]></Title><Artist><![CDATA[Me]]></Artist></Score>"]
    out.append("<MasterTrack><Automations>")
    for bar, pos, bpm, linear in tempo:
        out.append(
            f"<Automation><Type>Tempo</Type><Linear>{str(linear).lower()}</Linear><Bar>{bar}</Bar>"
            f"<Position>{pos}</Position><Value>{bpm} 2</Value></Automation>"
        )
    out.append("</Automations></MasterTrack><Tracks>")
    out.append("<Track id='0'><Name>Guitar</Name><InstrumentSet><Type>electricGuitar</Type><Elements><Element>"
               "<Name>Pitched</Name><Articulations><Articulation><Name></Name><InputMidiNumbers></InputMidiNumbers>"
               "<OutputMidiNumber>0</OutputMidiNumber></Articulation></Articulations></Element></Elements>"
               "</InstrumentSet><Staves><Staff/></Staves></Track>")
    out.append("<Track id='1'><Name>Drums</Name><InstrumentSet><Type>drumKit</Type><Elements>")
    for element, name, inp, outp in KIT:
        out.append(f"<Element><Name>{element}</Name><Articulations><Articulation><Name>{name}</Name>"
                   f"<InputMidiNumbers>{inp}</InputMidiNumbers><OutputMidiNumber>{outp}</OutputMidiNumber>"
                   "</Articulation></Articulations></Element>")
    out.append("</Elements></InstrumentSet><Staves><Staff/></Staves></Track></Tracks>")

    bars, voices, beats, notes, rhythms = [], [], [], [], []
    out.append("<MasterBars>")
    for mb in master_bars:
        out.append(f"<MasterBar><Time>{mb.time}</Time>")
        if mb.repeat:
            s, e, c = mb.repeat
            out.append(f"<Repeat start='{str(s).lower()}' end='{str(e).lower()}' count='{c}'/>")
        if mb.endings:
            out.append(f"<AlternateEndings>{mb.endings}</AlternateEndings>")
        if mb.section:
            out.append(f"<Section><Letter><![CDATA[{mb.section}]]></Letter><Text><![CDATA[]]></Text></Section>")
        guitar_bar = len(bars)
        bars.append("<Voices>-1 -1 -1 -1</Voices>")
        voice_ids = []
        for voice in mb.voices:
            beat_ids = []
            for b in voice:
                note_ids = []
                for n in b.notes:
                    note_ids.append(str(len(notes)))
                    notes.append(
                        (f"<Accent>{n.accent}</Accent>" if n.accent else "")
                        + ("<AntiAccent>Normal</AntiAccent>" if n.ghost else "")
                        + ("<Tie origin='false' destination='true'/>" if n.tie else "")
                        + f"<InstrumentArticulation>{n.articulation}</InstrumentArticulation>"
                    )
                rhythm = f"<NoteValue>{b.value}</NoteValue>"
                if b.dots:
                    rhythm += f"<AugmentationDot count='{b.dots}'/>"
                if b.tuplet:
                    rhythm += f"<PrimaryTuplet num='{b.tuplet[0]}' den='{b.tuplet[1]}'/>"
                rhythms.append(rhythm)
                beat_ids.append(str(len(beats)))
                beats.append(
                    (f"<GraceNotes>{b.grace}</GraceNotes>" if b.grace else "")
                    + f"<Dynamic>{b.dynamic}</Dynamic><Rhythm ref='{len(rhythms) - 1}'/>"
                    + (f"<Notes>{' '.join(note_ids)}</Notes>" if note_ids else "")
                )
            voice_ids.append(str(len(voices)))
            voices.append(f"<Beats>{' '.join(beat_ids)}</Beats>")
        drum_bar = len(bars)
        bars.append(f"<Voices>{' '.join(voice_ids + ['-1'] * (4 - len(voice_ids)))}</Voices>")
        out.append(f"<Bars>{guitar_bar} {drum_bar}</Bars></MasterBar>")
    out.append("</MasterBars>")
    for tag, items in (("Bar", bars), ("Voice", voices), ("Beat", beats), ("Note", notes), ("Rhythm", rhythms)):
        out.append(f"<{tag}s>" + "".join(f"<{tag} id='{i}'>{x}</{tag}>" for i, x in enumerate(items)) + f"</{tag}s>")
    out.append("</GPIF>")
    return "".join(out).encode()


def write_gp(path: Path, gpif: bytes) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("Content/score.gpif", gpif)
    path.write_bytes(buffer.getvalue())
    return path
