"""Estimate BPM and musical key from the audio (requires `essentia`, see requirements.txt).

Results are estimates: BPM is usually right for 4/4 dance music and the key is right most of
the time, but both can be off (typically half/double BPM, or a relative/neighbouring key).
The web UI therefore shows them as pending edits that you review before saving.
"""
import subprocess

import mutagen

SAMPLE_RATE = 44100
SEGMENT_SECONDS = 120          # analyse a window from the middle of long tracks (faster, skips intros)
BPM_RANGE = (70, 180)          # fold results into the range DJs actually play

_PITCH_CLASS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6,
                "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def camelot(key: str, scale: str) -> str:
    """('A', 'minor') -> '8A',  ('C', 'major') -> '8B'."""
    pc = _PITCH_CLASS[key]
    if scale == "minor":
        return f"{((pc + 3) % 12 * 7 + 7) % 12 + 1}A"  # use the relative major's position
    return f"{(pc * 7 + 7) % 12 + 1}B"


def _decode(path: str):
    """Decode (a window of) the file to mono float32 samples using ffmpeg."""
    import numpy as np

    try:
        length = mutagen.File(path).info.length
    except Exception:  # unreadable header: let ffmpeg decide below
        length = 0
    cmd = ["ffmpeg", "-v", "error"]
    if length > SEGMENT_SECONDS * 1.5:
        cmd += ["-ss", str((length - SEGMENT_SECONDS) / 2), "-t", str(SEGMENT_SECONDS)]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, timeout=120)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("Could not decode the audio file.")
    return np.frombuffer(out.stdout, dtype=np.float32)


def analyze(path: str) -> dict:
    """Return {"bpm": int, "key": "8A"}. Raises RuntimeError with a readable message on failure."""
    try:
        import essentia.standard as es
    except ImportError as e:
        raise RuntimeError("BPM/key analysis needs the essentia package, which is not installed (there is no Windows build of it).") from e

    audio = _decode(str(path))
    if len(audio) < SAMPLE_RATE * 5:
        raise RuntimeError("The track is too short to analyse.")

    bpm = es.RhythmExtractor2013(method="multifeature")(audio)[0]
    key, scale, _strength = es.KeyExtractor(profileType="edma")(audio)  # "edma" suits electronic music

    if bpm <= 0:
        raise RuntimeError("Could not detect a tempo.")
    while bpm < BPM_RANGE[0]:
        bpm *= 2
    while bpm >= BPM_RANGE[1]:
        bpm /= 2
    return {"bpm": round(bpm), "key": camelot(key, scale)}
