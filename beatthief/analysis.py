"""Estimate BPM and musical key from the audio.

Two engines, same result format:
  * essentia (see requirements.txt): the better one, used whenever it is installed. It has no
    Windows build, so it is skipped there.
  * a built-in estimator that needs only numpy (below): used when essentia is missing, so Windows
    users can analyse too. Compared with essentia on a library of techno it gives practically the
    same BPM, but its key agrees only about half of the time (more with relative/adjacent keys):
    in dance music the kick and sub swamp the harmony, and that is hard for any simple method.

Results are estimates either way: BPM can be off by half/double and the key by a relative or
neighbouring key. The web UI therefore shows them as pending edits that you review before saving.
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


# --------------------------------------------------------------------------- built-in estimator (numpy only)
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Shaath's key profiles (the ones KeyFinder uses; they suit dance music better than the classical ones).
_PROFILE_MAJOR = [6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4]
_PROFILE_MINOR = [6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2]


def _stft_magnitude(x, n_fft, hop, batch=2000):
    """Yield |STFT| for batches of frames (keeps memory small on long windows)."""
    import numpy as np

    window = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (len(x) - n_fft) // hop
    for start in range(0, n_frames, batch):
        end = min(n_frames, start + batch)
        idx = hop * np.arange(start, end)[:, None] + np.arange(n_fft)[None, :]
        yield np.abs(np.fft.rfft(x[idx] * window, axis=1))


def _onset_envelope(x, hop=256, n_fft=1024, n_bands=40):
    """Spectral flux on log-spaced bands: one value per `hop` samples, high where something hits."""
    import numpy as np

    freqs = np.fft.rfftfreq(n_fft, 1 / SAMPLE_RATE)
    edges = np.unique(np.searchsorted(freqs, np.geomspace(40, 15000, n_bands + 1)))
    bands = np.vstack([np.add.reduceat(mag, edges[:-1], axis=1) for mag in _stft_magnitude(x, n_fft, hop)])
    logm = np.log1p(100 * bands / (bands.max() or 1))
    flux = np.maximum(0, np.diff(logm, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(86) / 86, mode="same")       # drop the slow trend (~0.5 s)
    return np.maximum(flux, 0), SAMPLE_RATE / hop


def _tempo(x) -> float:
    """BPM from the autocorrelation of the onset envelope. A candidate tempo is scored by how well
    the envelope repeats at 1, 2, ... 6 beats, which pins it down far better than one peak; a mild
    preference for ~130 BPM settles half/double-time doubts the way dance music usually goes."""
    import numpy as np

    env, fps = _onset_envelope(x)
    n = len(env)
    spec = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac /= np.arange(n, 0, -1)                                       # unbiased
    ac /= ac[0] or 1
    bpms = np.arange(60, 200, 0.1)
    score = np.zeros_like(bpms)
    for m in range(1, 7):
        lag = m * 60 * fps / bpms
        ok = lag < n - 1
        i = lag[ok].astype(int)
        frac = lag[ok] - i
        values = np.zeros_like(bpms)
        values[ok] = ac[i] * (1 - frac) + ac[i + 1] * frac            # linear interpolation between lags
        score += values / m ** 0.5
    score *= np.exp(-0.5 * (np.log2(bpms / 130) / 0.8) ** 2)
    return float(bpms[int(np.argmax(score))])


def _key(x):
    """(tonic, scale) from a chromagram of the tonal peaks between 120 Hz and 2 kHz. Everything
    below ~120 Hz is ignored on purpose: in dance music that is the kick and sub, whose pitch says
    little about the harmony. Each frame is normalised so loud sections don't dominate."""
    import numpy as np

    n_fft, hop = 8192, 4096
    freqs = np.fft.rfftfreq(n_fft, 1 / SAMPLE_RATE)
    sel = (freqs >= 120) & (freqs <= 2000)
    pitch_class = np.round(12 * np.log2(freqs[sel] / 440.0) + 69).astype(int) % 12
    chroma = np.zeros(12)
    for mag in _stft_magnitude(x, n_fft, hop, batch=200):
        m = mag[:, sel]
        left = np.pad(m, ((0, 0), (1, 0)))[:, :-1]
        right = np.pad(m, ((0, 0), (0, 1)))[:, 1:]
        peaks = np.where((m >= left) & (m >= right), m, 0)
        peaks = np.where(peaks >= 0.10 * peaks.max(axis=1, keepdims=True), peaks, 0)   # drop weak peaks (noise)
        per_frame = np.stack([peaks[:, pitch_class == k].sum(axis=1) for k in range(12)], axis=1)
        chroma += (per_frame / (per_frame.sum(axis=1, keepdims=True) + 1e-9)).sum(axis=0)
    best = (-2.0, 0, "major")
    for tonic in range(12):
        for scale, profile in (("major", _PROFILE_MAJOR), ("minor", _PROFILE_MINOR)):
            r = np.corrcoef(chroma, np.roll(profile, tonic))[0, 1]
            if r > best[0]:
                best = (r, tonic, scale)
    return _NOTES[best[1]], best[2]


def _analyze_builtin(audio) -> dict:
    return {"bpm": _tempo(audio), "key": camelot(*_key(audio))}


def analyze(path: str) -> dict:
    """Return {"bpm": int, "key": "8A", "engine": "essentia"|"builtin"}.
    Raises RuntimeError with a readable message on failure."""
    audio = _decode(str(path))
    if len(audio) < SAMPLE_RATE * 5:
        raise RuntimeError("The track is too short to analyse.")

    try:
        import essentia.standard as es
    except ImportError:
        result, engine = _analyze_builtin(audio), "builtin"      # e.g. Windows: no essentia build exists
    else:
        bpm = es.RhythmExtractor2013(method="multifeature")(audio)[0]
        key, scale, _strength = es.KeyExtractor(profileType="edma")(audio)  # "edma" suits electronic music
        result, engine = {"bpm": bpm, "key": camelot(key, scale)}, "essentia"

    bpm = result["bpm"]
    if bpm <= 0:
        raise RuntimeError("Could not detect a tempo.")
    while bpm < BPM_RANGE[0]:
        bpm *= 2
    while bpm >= BPM_RANGE[1]:
        bpm /= 2
    return {"bpm": round(bpm), "key": result["key"], "engine": engine}
