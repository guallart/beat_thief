"""Per-platform rate limiting, so heavy use doesn't get the IP blocked (HTTP 429).

Defaults are deliberately conservative: yt-dlp's usual advice is 5-10 s between
downloads, and anonymous YouTube sessions are reported to start failing at roughly
300 videos/hour. Override any value from config.json under "rate_limit".

Each download reserves a start slot under a lock, then sleeps until its slot, so it is
safe with any number of concurrent threads. The history is kept in memory, per process.
"""
import random
import threading
import time
from collections import defaultdict, deque

from . import config

DEFAULTS = {
    "min_gap": 5.0,          # seconds between two downloads of the same platform
    "jitter": 5.0,           # plus a random 0..jitter seconds, so requests aren't periodic
    "max_per_window": 20,    # downloads allowed per window and platform
    "window": 600.0,         # window length in seconds (20 per 10 min = 120/hour)
}


def limits() -> dict:
    custom = config.get_setting("rate_limit", {}) or {}
    merged = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        try:
            merged[key] = type(default)(custom.get(key, default))
        except (TypeError, ValueError):
            pass
    return merged


# Accepted ranges when the user edits the limits (the values are in the same units as DEFAULTS).
BOUNDS = {"min_gap": (0, 3600), "jitter": (0, 3600), "max_per_window": (1, 10000), "window": (10, 86400)}


def validate_limits(data) -> dict:
    """Check user-supplied limits and return a clean dict. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("Invalid rate limit settings.")
    clean = {}
    for key, default in DEFAULTS.items():
        try:
            value = type(default)(data.get(key, default))
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be a number.") from None
        low, high = BOUNDS[key]
        if not low <= value <= high:
            raise ValueError(f"'{key}' must be between {low} and {high}.")
        clean[key] = value
    return clean


_lock = threading.Lock()
_history = defaultdict(deque)  # source -> reserved start times, ascending


def _next_slot(source: str, lim: dict, now: float) -> float:
    history = _history[source]
    while history and history[0] < now - lim["window"]:
        history.popleft()

    slot = now
    if history:
        slot = max(slot, history[-1] + lim["min_gap"] + random.uniform(0, lim["jitter"]))
    if len(history) >= lim["max_per_window"]:
        slot = max(slot, history[-lim["max_per_window"]] + lim["window"])
    return slot


def estimate_wait(source: str) -> float:
    """Seconds a new download would wait right now (does not reserve a slot)."""
    lim = limits()
    with _lock:
        # Deterministic part only: no jitter, no mutation of the history.
        now = time.time()
        history = [t for t in _history[source] if t >= now - lim["window"]]
        slot = now
        if history:
            slot = max(slot, history[-1] + lim["min_gap"])
        if len(history) >= lim["max_per_window"]:
            slot = max(slot, history[-lim["max_per_window"]] + lim["window"])
        return max(0.0, slot - now)


def wait_turn(source: str, on_wait=None):
    """Block until it's this download's turn. `on_wait(seconds_left)` is called about once
    per second while waiting, and once with 0 when the wait is over."""
    lim = limits()
    with _lock:
        slot = _next_slot(source or "other", lim, time.time())
        _history[source or "other"].append(slot)

    waited = False
    while True:
        remaining = slot - time.time()
        if remaining <= 0:
            break
        waited = True
        if on_wait:
            on_wait(remaining)
        time.sleep(min(1.0, remaining))
    if waited and on_wait:
        on_wait(0)
