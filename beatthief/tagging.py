"""Read, write and auto-fill audio tags (WAV, AIFF, MP3, FLAC, OGG/Opus, M4A).

Editable fields: title, artist, album, year, genre, bpm, key, comment.
WAV/AIFF/MP3 use ID3 frames; FLAC/OGG/Opus use Vorbis comments; M4A uses MP4 atoms.
"""
import re
import subprocess
import urllib.request
from pathlib import Path

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, COMM, TALB, TBPM, TCON, TDRC, TIT2, TKEY, TPE1, WOAS)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

FIELDS = ("title", "artist", "album", "year", "genre", "bpm", "key", "comment")
MAX_LEN = 1000

ID3_FORMATS = {".wav": WAVE, ".aiff": AIFF, ".aif": AIFF, ".mp3": MP3}
VORBIS_FORMATS = {".flac": FLAC, ".ogg": OggVorbis, ".opus": OggOpus}
MP4_FORMATS = {".m4a": MP4, ".mp4": MP4}
SUPPORTED_EXTS = set(ID3_FORMATS) | set(VORBIS_FORMATS) | set(MP4_FORMATS)

_ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "year": "TDRC",
               "genre": "TCON", "bpm": "TBPM", "key": "TKEY"}
_ID3_CLASSES = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TDRC": TDRC,
                "TCON": TCON, "TBPM": TBPM, "TKEY": TKEY}
_VORBIS_KEYS = {"title": "title", "artist": "artist", "album": "album", "year": "date",
                "genre": "genre", "bpm": "bpm", "key": "initialkey", "comment": "comment"}
_MP4_ATOMS = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "year": "\xa9day",
              "genre": "\xa9gen", "comment": "\xa9cmt"}
_MP4_KEY_ATOM = "----:com.apple.iTunes:initialkey"


def is_supported(path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTS


def _clean(value) -> str:
    return re.sub(r"[\x00-\x1f]", " ", str(value or "")).strip()[:MAX_LEN]


# --------------------------------------------------------------------------- reading
def read_tags(path) -> dict:
    """Return {"tags": {field: str}, "has_cover": bool, "duration": float|None}."""
    ext = Path(path).suffix.lower()
    tags = dict.fromkeys(FIELDS, "")
    has_cover, duration = False, None

    audio = mutagen.File(str(path))
    if audio is None:
        return {"tags": tags, "has_cover": False, "duration": None}
    duration = getattr(audio.info, "length", None)

    if ext in ID3_FORMATS and audio.tags is not None:
        t = audio.tags
        for field, frame in _ID3_FRAMES.items():
            if frame in t:
                tags[field] = _clean(" / ".join(str(x) for x in t[frame].text))
        comms = t.getall("COMM")
        if comms:
            tags["comment"] = _clean(" ".join(comms[0].text))
        has_cover = bool(t.getall("APIC"))
    elif ext in VORBIS_FORMATS and audio.tags is not None:
        for field, key in _VORBIS_KEYS.items():
            tags[field] = _clean(" / ".join(audio.tags.get(key, [])))
        if ext == ".flac":
            has_cover = bool(audio.pictures)
    elif ext in MP4_FORMATS and audio.tags is not None:
        t = audio.tags
        for field, atom in _MP4_ATOMS.items():
            tags[field] = _clean(" / ".join(str(x) for x in t.get(atom, [])))
        if t.get("tmpo"):
            tags["bpm"] = str(t["tmpo"][0])
        if t.get(_MP4_KEY_ATOM):
            tags["key"] = _clean(bytes(t[_MP4_KEY_ATOM][0]).decode("utf-8", "replace"))
        has_cover = bool(t.get("covr"))

    return {"tags": tags, "has_cover": has_cover, "duration": duration}


def read_cover(path):
    """Return (mime, bytes) of the embedded cover, or None."""
    ext = Path(path).suffix.lower()
    audio = mutagen.File(str(path))
    if audio is None or audio.tags is None:
        return None
    if ext in ID3_FORMATS:
        pics = audio.tags.getall("APIC")
        return (pics[0].mime, pics[0].data) if pics else None
    if ext == ".flac" and audio.pictures:
        return audio.pictures[0].mime, audio.pictures[0].data
    if ext in MP4_FORMATS and audio.tags.get("covr"):
        cover = audio.tags["covr"][0]
        return ("image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"), bytes(cover)
    return None


# --------------------------------------------------------------------------- writing
def write_tags(path, tags: dict, cover: bytes = None):
    """Write the given fields (others untouched; an empty string clears a field).
    `cover` (JPEG/PNG bytes) is embedded when given. Raises on failure."""
    ext = Path(path).suffix.lower()
    values = {f: _clean(tags[f]) for f in FIELDS if f in tags}

    if ext in ID3_FORMATS:
        audio = ID3_FORMATS[ext](str(path))
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags
        for field, value in values.items():
            if field == "comment":
                t.delall("COMM")
                if value:
                    t.add(COMM(encoding=3, lang="eng", desc="", text=[value]))
                continue
            frame = _ID3_FRAMES[field]
            t.delall(frame)
            if value:
                t.add(_ID3_CLASSES[frame](encoding=3, text=[value]))
        if cover:
            mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
            t.delall("APIC")
            t.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
        if "source_url" in tags:
            t.delall("WOAS")
            if tags["source_url"]:
                t.add(WOAS(url=tags["source_url"]))
        audio.save()

    elif ext in VORBIS_FORMATS:
        audio = VORBIS_FORMATS[ext](str(path))
        if audio.tags is None:
            audio.add_tags()
        for field, value in values.items():
            key = _VORBIS_KEYS[field]
            if value:
                audio.tags[key] = [value]
            elif key in audio.tags:
                del audio.tags[key]
        if cover and ext == ".flac":
            pic = Picture()
            pic.type, pic.desc, pic.data = 3, "Cover", cover
            pic.mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
            audio.clear_pictures()
            audio.add_picture(pic)
        audio.save()

    elif ext in MP4_FORMATS:
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags
        for field, value in values.items():
            if field == "bpm":
                if value.isdigit():
                    t["tmpo"] = [int(value)]
                elif not value:
                    t.pop("tmpo", None)
                else:
                    raise ValueError("BPM must be a whole number for M4A files.")
            elif field == "key":
                if value:
                    t[_MP4_KEY_ATOM] = [MP4FreeForm(value.encode("utf-8"))]
                else:
                    t.pop(_MP4_KEY_ATOM, None)
            elif value:
                t[_MP4_ATOMS[field]] = [value]
            else:
                t.pop(_MP4_ATOMS[field], None)
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
            t["covr"] = [MP4Cover(cover, imageformat=fmt)]
        audio.save()

    else:
        raise ValueError(f"Unsupported format: {ext}")


# --------------------------------------------------------------------------- auto-fill
_NOISE = re.compile(
    r"^(official( (music|lyric|lyrics|video|audio|visualizer|visualiser))*|(music |lyric |lyrics |hd |hq )?video|"
    r"lyrics?|lyric video|audio|visuali[sz]er|hd|hq|4k|1080p|full (song|track|version)|"
    r"free download|out now|premiere|clip officiel)$", re.I)
_BRACKETS = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
_ARTIST_TITLE = re.compile(r"^\s*(.+?)\s+[-–—]\s+(.+?)\s*$")


def _strip_noise(title: str) -> str:
    """Remove '(Official Video)', '[HD]' and similar, but keep '(Original Mix)', '(Remix)'."""
    def keep(m):
        return "" if _NOISE.match(m.group(1).strip()) else m.group(0)
    return _BRACKETS.sub(keep, title).strip()


def _clean_artist(name: str) -> str:
    name = re.sub(r"\s*-\s*Topic$", "", name or "", flags=re.I)
    name = re.sub(r"(?<=[a-z])VEVO$", "", name)
    return name.strip()


def tags_from_info(info: dict) -> dict:
    """Build the tag dict from a yt-dlp info dict (YouTube, SoundCloud, ...)."""
    artists = info.get("artists")
    artist = info.get("artist") or (", ".join(artists) if artists else "")
    explicit_artist = bool(artist or info.get("track"))
    artist = artist or info.get("creator") or ""
    title = _strip_noise(info.get("track") or info.get("title") or "")

    m = _ARTIST_TITLE.match(title)
    if m and not info.get("track"):
        first, rest = m.groups()
        if artist and first.lower() == artist.lower():
            title = rest
        elif not explicit_artist:
            artist, title = first, rest
    if not artist:
        artist = info.get("uploader") or info.get("channel") or ""

    genres = info.get("genres") or ([info["genre"]] if info.get("genre") else [])
    year = str(info.get("release_year") or (info.get("release_date") or "")[:4]
               or (info.get("upload_date") or "")[:4] or "")

    return {
        "title": title,
        "artist": _clean_artist(artist),
        "album": info.get("album") or "",
        "year": year,
        "genre": ", ".join(genres),
        "comment": info.get("webpage_url") or info.get("original_url") or "",
        "source_url": info.get("webpage_url") or "",
    }


def _best_thumbnail_url(info: dict):
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if not thumbs:
        return info.get("thumbnail")
    # Prefer jpg/png (the most compatible), then the explicit preference / largest size.
    def score(t):
        ext_ok = t["url"].split("?")[0].lower().endswith((".jpg", ".jpeg", ".png"))
        return (ext_ok, t.get("preference") or 0, (t.get("width") or 0) * (t.get("height") or 0))
    return max(thumbs, key=score)["url"]


def fetch_cover(info: dict, max_bytes: int = 10_000_000):
    """Download the cover art and return JPEG/PNG bytes, or None."""
    url = _best_thumbnail_url(info)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(max_bytes)
    except Exception:
        return None
    if data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG":
        return data
    # WebP and others: convert to JPEG with ffmpeg.
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-f", "mjpeg", "pipe:1"],
            input=data, capture_output=True, timeout=30, check=True)
        return out.stdout or None
    except Exception:
        return None


def tag_downloaded_file(path, info: dict):
    """Fill every tag yt-dlp knows about (plus cover art). Never raises: returns an error
    string on failure so the download itself is not lost."""
    try:
        write_tags(path, tags_from_info(info), cover=fetch_cover(info))
        return None
    except Exception as e:
        return str(e)
