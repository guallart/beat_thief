"""URL detection and cleaning for the supported music sources."""
import re
from urllib.parse import urlparse, parse_qs

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
SOUNDCLOUD_HOSTS = {
    "soundcloud.com", "www.soundcloud.com", "m.soundcloud.com",
    "on.soundcloud.com",  # short share links
}


def extract_url(text: str):
    """Return the first URL in the text (adding https:// if the scheme is missing)."""
    match = re.search(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}/[^\s]*", text.strip(), re.IGNORECASE)
    if not match:
        return None
    url = match.group(0)
    return url if url.startswith("http") else "https://" + url


def detect_source(url: str):
    """Return 'youtube', 'soundcloud' or None."""
    host = (urlparse(url).hostname or "").lower()
    if host in YOUTUBE_HOSTS:
        return "youtube"
    if host in SOUNDCLOUD_HOSTS:
        return "soundcloud"
    return None


def _youtube_video_id(url: str):
    parsed = urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        return parsed.path.lstrip("/") or None
    if parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
        return parsed.path.split("/")[2] if parsed.path.count("/") >= 2 else None
    return parse_qs(parsed.query).get("v", [None])[0]


def _soundcloud_track_id(url: str):
    parsed = urlparse(url)
    if parsed.hostname == "on.soundcloud.com":
        return parsed.path.strip("/") or None
    parts = [p for p in parsed.path.split("/") if p]
    # artist/track is a track; artist alone, artist/sets/... etc. are not.
    if len(parts) == 2 and parts[1] not in {"sets", "tracks", "albums", "popular-tracks", "likes", "reposts"}:
        return "/".join(parts)
    if len(parts) == 3 and parts[1] == "sets":
        return "/".join(parts)
    return None


def validate_and_clean_url(user_input: str):
    """Return (ok, url_or_error_message, source, item_id)."""
    url = extract_url(user_input)
    if not url:
        return False, "I couldn't find a link in your message.", None, None

    source = detect_source(url)
    if source is None:
        return False, "Unsupported link. Send a YouTube or SoundCloud URL.", None, None

    if source == "youtube":
        item_id = _youtube_video_id(url)
        if not item_id:
            return False, "Could not extract the video ID. Check that the URL is correct.", None, None
    else:
        item_id = _soundcloud_track_id(url)
        if not item_id:
            return False, "That doesn't look like a SoundCloud track link.", None, None

    return True, url, source, item_id
