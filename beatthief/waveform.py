"""Three-band overview waveform of a whole track (low / mid / high), for the player's seek bar.

The track is decoded with ffmpeg, cut into BINS equal slices and each slice is split into bands
with an FFT. Every band is normalised on its own against the loudest parts of that band, so the
shape shows the structure of the track (a break is where the low band disappears, vocals and
leads show up in the mid band) rather than absolute levels.
"""
import subprocess

SAMPLE_RATE = 22050            # plenty for a visual overview, and half the work of 44.1 kHz
BINS = 1000                    # horizontal resolution of the overview
MAX_SECONDS = 30 * 60          # don't decode more than this (long DJ mixes)
# Same crossovers as the player's equalizer (EQ_XOVER in index.html), so what you see is what each knob moves.
BANDS = (("low", 0, 250), ("mid", 250, 3000), ("high", 3000, SAMPLE_RATE / 2))


def _decode(path: str):
    import numpy as np

    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-t", str(MAX_SECONDS), "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, timeout=180)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("Could not decode the audio file.")
    return np.frombuffer(out.stdout, dtype=np.float32)


def compute(path: str) -> dict:
    """Return {"low": [...], "mid": [...], "high": [...]}: BINS integers 0-255 per band."""
    try:
        import numpy as np
    except ImportError as e:
        raise RuntimeError("numpy is not installed (run: python -m pip install -r requirements.txt)") from e
    audio = _decode(str(path))
    if len(audio) < SAMPLE_RATE:
        raise RuntimeError("The track is too short.")
    size = len(audio) // BINS
    slices = audio[: size * BINS].reshape(BINS, size)
    freqs = np.fft.rfftfreq(size, 1 / SAMPLE_RATE)
    masks = {name: (freqs >= lo) & (freqs < hi) for name, lo, hi in BANDS}
    window = np.hanning(size).astype(np.float32)

    energy = {name: np.zeros(BINS, dtype=np.float32) for name in masks}
    for start in range(0, BINS, 100):                       # batches keep memory modest
        power = np.abs(np.fft.rfft(slices[start:start + 100] * window, axis=1)) ** 2
        for name, mask in masks.items():
            energy[name][start:start + 100] = np.sqrt(power[:, mask].sum(axis=1))

    result = {}
    for name, values in energy.items():
        ref = np.percentile(values, 98) or 1.0               # ignore the odd spike
        norm = np.clip(values / ref, 0, 1) ** 1.3            # slight gamma > 1 sharpens the contrast between sections
        result[name] = np.round(norm * 255).astype(int).tolist()
    return result
