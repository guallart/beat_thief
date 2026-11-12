"""Convert audio files between formats with ffmpeg (no extra dependencies)."""
import re
import subprocess
import tempfile
from pathlib import Path

from .config import get_download_dir
from .log import log

# format -> (label, ffmpeg codec arguments). Lossless formats keep the source quality.
FORMATS = {
    "wav":  ("WAV (lossless)",       ["-c:a", "pcm_s16le"]),
    "aiff": ("AIFF (lossless)",      ["-c:a", "pcm_s16be"]),
    "flac": ("FLAC (lossless)",      ["-c:a", "flac"]),
    "mp3":  ("MP3 320 kbps",         ["-c:a", "libmp3lame", "-b:a", "320k"]),
    "m4a":  ("M4A (AAC 256 kbps)",   ["-c:a", "aac", "-b:a", "256k"]),
    "ogg":  ("OGG Vorbis (~256 kbps)", ["-c:a", "libvorbis", "-q:a", "8"]),
    "opus": ("Opus 192 kbps",        ["-c:a", "libopus", "-b:a", "192k"]),
}
# Formats that can carry cover art as an attached picture.
COVER_FORMATS = {"mp3", "m4a", "flac"}


def _clean_stem(filename: str) -> str:
    stem = Path(filename.replace("\\", "/").split("/")[-1]).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return stem or "audio"


def _free_path(folder: Path, stem: str, ext: str) -> Path:
    path = folder / f"{stem}.{ext}"
    n = 1
    while path.exists():
        path = folder / f"{stem} ({n}).{ext}"
        n += 1
    return path


def _run(src: Path, dest: Path, audio_format: str, with_cover: bool):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-map_metadata", "0"]
    if with_cover:
        cmd += ["-map", "0:v:0", "-c:v", "copy", "-disposition:v", "attached_pic"]
    cmd += FORMATS[audio_format][1] + [str(dest)]
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        log.debug("ffmpeg exited with %d: %s", result.returncode, (result.stderr or "").strip())
    return result


def convert(upload, filename: str, audio_format: str) -> Path:
    """Convert the uploaded file (a werkzeug FileStorage) into the download folder and return the new path.

    Tags are kept; cover art too, when the target format supports it. Raises RuntimeError on failure."""
    if audio_format not in FORMATS:
        raise ValueError("Unsupported format.")
    out_dir = get_download_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".tmp-") as work:
        src = Path(work) / "input"
        upload.save(src)
        dest = Path(work) / f"output.{audio_format}"
        result = _run(src, dest, audio_format, audio_format in COVER_FORMATS)
        if result.returncode != 0:  # most likely no cover art in the file: try again without it
            result = _run(src, dest, audio_format, False)
        if result.returncode != 0 or not dest.exists():
            lines = (result.stderr or "").strip().splitlines()
            raise RuntimeError(lines[-1].rsplit(": ", 1)[-1] if lines else "ffmpeg could not convert this file.")
        final = _free_path(out_dir, _clean_stem(filename), audio_format)
        dest.replace(final)
    return final
