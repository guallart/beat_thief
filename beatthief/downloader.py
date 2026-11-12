"""Download audio from YouTube or SoundCloud (any site yt-dlp supports) as WAV, FLAC, MP3 or M4A."""
import os
import shutil
import tempfile

import yt_dlp

from . import throttle
from .log import YtdlpLogger, log, verbose
from .config import get_download_dir, get_setting
from .sources import detect_source
from .tagging import tag_downloaded_file

# The most common formats. The value is the bitrate in kbps for lossy formats (None = lossless).
FORMATS = {"wav": None, "flac": None, "mp3": "320", "m4a": "256"}
AUDIO_FORMAT = "wav"  # built-in default; the user's choice lives in config.json ("default_format")


def default_format() -> str:
    fmt = get_setting("default_format", AUDIO_FORMAT)
    return fmt if fmt in FORMATS else AUDIO_FORMAT


MAX_PLAYLIST = 500  # tracks taken from one playlist


def list_playlist(url: str, source: str):
    """Break a playlist (or SoundCloud set) into its tracks without downloading anything.
    Returns [{"url": ..., "title": ...}]. Raises on failure."""
    opts = {"extract_flat": "in_playlist", "skip_download": True, "quiet": True, "no_warnings": True,
            "playlistend": MAX_PLAYLIST}
    if verbose():
        opts.update({"verbose": True, "logger": YtdlpLogger(), "no_warnings": False})
    log.info("Reading playlist %s", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    tracks = []
    for entry in filter(None, (info or {}).get("entries") or []):
        title = entry.get("title")
        if title in ("[Private video]", "[Deleted video]"):
            continue
        link = f"https://www.youtube.com/watch?v={entry['id']}" if source == "youtube" and entry.get("id") \
            else entry.get("url") or entry.get("webpage_url")
        if link:
            tracks.append({"url": link, "title": title})
    log.info("Playlist has %d usable tracks", len(tracks))
    return tracks


def _build_opts(workdir: str, playlist: bool, audio_format: str, on_progress=None):
    opts = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": FORMATS[audio_format],
        }],
        "outtmpl": os.path.join(
            workdir,
            "%(playlist_index)s - %(title)s.%(ext)s" if playlist else "%(title)s.%(ext)s",
        ),
        "noplaylist": not playlist,
        "ignoreerrors": playlist,  # skip unavailable entries inside playlists
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "sleep_interval_requests": 1,  # be gentle with the platform's API
    }
    if playlist:
        opts["sleep_interval"] = 5       # random pause between tracks of a playlist
        opts["max_sleep_interval"] = 10

    if verbose():
        opts.update({"verbose": True, "logger": YtdlpLogger(), "no_warnings": False})

    if on_progress:
        def hook(d):
            title = (d.get("info_dict") or {}).get("title")
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                pct = min(100.0, d["downloaded_bytes"] * 100 / total) if total else None
                on_progress("downloading", pct, title)
            elif d["status"] == "finished":
                on_progress("converting", 100.0, title)

        opts["progress_hooks"] = [hook]

    return opts


def fetch_audio(url: str, playlist: bool = False, on_progress=None, on_wait=None,
                audio_format: str = None):
    """Download `url` as `audio_format` (one of FORMATS) into the configured download dir and return the list of file paths.

    Raises on failure. `on_progress(stage, percent, title)` is called while running
    (stage is "downloading" or "converting"; percent may be None).
    `on_wait(seconds_left)` is called while the rate limiter (throttle.py) makes it wait.
    Works for YouTube, SoundCloud and anything else yt-dlp can handle.
    """
    audio_format = audio_format or default_format()
    if audio_format not in FORMATS:
        raise ValueError(f"Unsupported format: {audio_format}")

    log.info("Downloading %s as %s", url, audio_format)
    throttle.wait_turn(detect_source(url) or "other", on_wait)
    log.debug("Rate limiter released %s", url)

    downloads_dir = str(get_download_dir())
    os.makedirs(downloads_dir, exist_ok=True)

    # Download into a private temp dir so concurrent requests don't mix files.
    with tempfile.TemporaryDirectory(dir=downloads_dir, prefix=".tmp-") as workdir:
        with yt_dlp.YoutubeDL(_build_opts(workdir, playlist, audio_format, on_progress)) as ydl:
            info = ydl.extract_info(url, download=True)

        # Fill title/artist/album/year/genre/comment and cover art from what the platform gave us.
        entries = (info or {}).get("entries") or [info]
        for entry in filter(None, entries):
            for item in entry.get("requested_downloads") or []:
                path = item.get("filepath")
                if path and os.path.exists(path):
                    error = tag_downloaded_file(path, entry)
                    if error:
                        log.warning("Could not tag %s: %s", os.path.basename(path), error)

        paths = []
        for name in sorted(os.listdir(workdir)):
            if not name.endswith("." + audio_format):
                continue
            dest = os.path.join(downloads_dir, name)
            shutil.move(os.path.join(workdir, name), dest)  # overwrites existing
            paths.append(dest)

    if not paths:
        raise RuntimeError("No audio file was produced.")
    log.info("Saved %s", ", ".join(paths))
    return paths


def download_audio(url: str, playlist: bool = False, audio_format: str = None):
    """Non-raising wrapper: returns (success, paths), with paths empty on failure."""
    try:
        return True, fetch_audio(url, playlist, audio_format=audio_format)
    except Exception as e:
        log.error("Download failed: %s", e, exc_info=verbose())
        return False, []
