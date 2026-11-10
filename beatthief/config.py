"""Persistent user settings, stored in config.json at the project root."""
import json
import os
from pathlib import Path

# BEATTHIEF_CONFIG lets tests (or a second instance) use a different settings file.
CONFIG_FILE = Path(os.environ.get("BEATTHIEF_CONFIG") or Path(__file__).resolve().parent.parent / "config.json")
DEFAULT_DOWNLOAD_DIR = Path.home() / "Music" / "Unclassified"


def _load() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_setting(key: str, default=None):
    return _load().get(key, default)


def set_setting(key: str, value):
    """Persist one setting; a value of None removes it (back to the default)."""
    data = _load()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_download_dir() -> Path:
    """Current download directory (read on every call so changes apply immediately)."""
    value = _load().get("download_dir")
    return Path(value).expanduser() if value else DEFAULT_DOWNLOAD_DIR


def set_download_dir(path: str) -> Path:
    """Validate, create and persist a new download directory. Raises ValueError."""
    path = (path or "").strip()
    if not path:
        raise ValueError("The path cannot be empty.")

    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError("Use an absolute path (or one starting with ~).")
    target = target.resolve()

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create the directory: {e.strerror or e}") from e
    if not target.is_dir() or not os.access(target, os.W_OK | os.X_OK):
        raise ValueError("The directory is not writable.")

    set_setting("download_dir", str(target))
    return target
