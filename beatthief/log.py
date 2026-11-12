"""Logging for BeatThief. Quiet by default; with --verbose (or BEATTHIEF_VERBOSE=1) everything goes to the
console, including yt-dlp's own debug output. --log-file also writes a full (debug) log to a file, which is
the thing to attach to a bug report."""
import importlib.util
import logging
import os
import platform
import shutil
import subprocess
import sys

from . import config

log = logging.getLogger("beatthief")
_verbose = False
_console = None


def verbose() -> bool:
    return _verbose


class YtdlpLogger:
    """Passed to yt-dlp so its output (including the [debug] lines) goes through our log."""
    def debug(self, msg):
        log.debug("yt-dlp: %s", msg)

    info = debug

    def warning(self, msg):
        log.warning("yt-dlp: %s", msg)

    def error(self, msg):
        log.error("yt-dlp: %s", msg)


def set_verbose(on: bool):
    """Switch the console between normal and verbose while running (the Settings toggle)."""
    global _verbose
    _verbose = bool(on)
    if _console is not None:
        _console.setLevel(logging.DEBUG if _verbose else logging.INFO)
    log.info("Verbose logging %s", "on" if _verbose else "off")
    if _verbose:
        log_environment()


def setup(verbose=False, log_file=None):
    global _verbose, _console
    _verbose = (bool(verbose) or bool(config.get_setting("verbose", False))
                or os.environ.get("BEATTHIEF_VERBOSE", "").lower() not in ("", "0", "false", "no"))
    log.handlers.clear()
    log.propagate = False
    log.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if _verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.addHandler(console)
    _console = console

    if log_file:
        try:
            handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as e:
            log.warning("Cannot write the log file %s: %s", log_file, e)
        else:
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
            log.addHandler(handler)
    if _verbose or log_file:
        log_environment()


def _run(*cmd) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:
        return "unavailable"


def log_environment():
    """What a bug report needs: versions and where things are."""
    from . import updater
    log.info("BeatThief %s", _run("git", "-C", str(updater.ROOT), "rev-parse", "--short", "HEAD"))
    log.info("Python %s on %s", sys.version.split()[0], platform.platform())
    try:
        import yt_dlp.version
        log.info("yt-dlp %s", yt_dlp.version.__version__)
    except Exception as e:
        log.info("yt-dlp unavailable: %s", e)
    extras = {name: bool(importlib.util.find_spec(name)) for name in ("yt_dlp_ejs", "curl_cffi", "essentia", "numpy")}
    log.info("Optional packages: %s", ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in extras.items()))
    log.info("ffmpeg: %s", _run("ffmpeg", "-version") if shutil.which("ffmpeg") else "NOT FOUND on PATH")
    for runtime in ("deno", "node", "bun"):
        if shutil.which(runtime):
            note = "" if runtime == "deno" else " (found, but yt-dlp only uses Deno by default)"
            log.info("JS runtime %s: %s%s", runtime, _run(runtime, "--version"), note)
    if not shutil.which("deno"):
        log.info("JS runtime deno: not found (YouTube may need it)")
    log.info("Download folder: %s", config.get_download_dir())
    log.info("Config file: %s", config.CONFIG_FILE)
