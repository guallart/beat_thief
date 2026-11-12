"""Look for new commits on the git remote and update in place (git pull), then restart.

Meant for people who cloned the repository: when the web interface starts it fetches the remote in
the background and, if there is something new, the UI lists the new commit messages and offers to
update. Everything here is standard library + the `git` command, so it behaves the same on Linux
and Windows. Set BEATTHIEF_NO_UPDATE_CHECK=1 to switch the check off.

Restarting is done by the supervisor in __main__.py: after a successful pull this process exits
with RESTART (or RESTART_AND_INSTALL when requirements.txt changed) and the supervisor starts it
again, installing the new requirements first, while nothing is running (on Windows packages that
are loaded can't be replaced).
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .log import log

ROOT = Path(__file__).resolve().parent.parent
RESTART = 75                  # exit codes understood by the supervisor
RESTART_AND_INSTALL = 76
MAX_COMMITS = 50
BOOT_ID = uuid.uuid4().hex    # changes on every start: the UI uses it to notice the restart

_lock = threading.Lock()
_info = {"state": "idle"}     # see get_state()


def supervised() -> bool:
    """True when started through the supervisor, i.e. we can ask to be restarted."""
    return os.environ.get("BEATTHIEF_SUPERVISED") == "1"


def _git(*args, timeout=30):
    # Never wait for a password or open a credential dialog: an update check must stay silent.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env, creationflags=flags)


def _out(*args, **kw) -> str:
    result = _git(*args, **kw)
    return result.stdout.strip() if result.returncode == 0 else ""


def check() -> dict:
    """Fetch the remote and report what is new. Never raises."""
    if os.environ.get("BEATTHIEF_NO_UPDATE_CHECK"):
        return {"state": "unavailable", "message": "Update check disabled."}
    try:
        if not (ROOT / ".git").exists():
            return {"state": "unavailable", "message": "Not a git checkout."}
        if not _out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return {"state": "unavailable", "message": "The current branch doesn't track a remote branch."}
        fetch = _git("fetch", "--quiet", timeout=25)
        if fetch.returncode != 0:
            log.warning("git fetch failed: %s", fetch.stderr.strip())
            return {"state": "error", "message": (fetch.stderr.strip().splitlines() or ["Could not reach the remote."])[0]}
        counts = _out("rev-list", "--left-right", "--count", "HEAD...@{u}").split()
        ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
        if behind == 0:
            return {"state": "uptodate", "ahead": ahead, "behind": 0, "commits": []}
        commits = []
        for line in _out("log", f"--max-count={MAX_COMMITS}", "--format=%h%x1f%s%x1f%an%x1f%cs", "HEAD..@{u}").splitlines():
            h, subject, author, date = (line.split("\x1f") + ["", "", "", ""])[:4]
            commits.append({"hash": h, "subject": subject, "author": author, "date": date})
        return {
            "state": "available", "ahead": ahead, "behind": behind, "commits": commits,
            "latest": _out("rev-parse", "--short", "@{u}"),
            "dirty": bool(_out("status", "--porcelain", "--untracked-files=no")),
        }
    except (OSError, subprocess.SubprocessError) as e:      # git not installed, timeout...
        log.warning("Update check failed: %s", e)
        return {"state": "error", "message": str(e) or "Could not check for updates."}


def get_state() -> dict:
    with _lock:
        return dict(_info)


def start_check():
    """Check in the background (the fetch can take a few seconds). Ignored if busy."""
    with _lock:
        if _info["state"] in ("checking", "applying", "restarting"):
            return
        _info.clear()
        _info["state"] = "checking"

    def work():
        result = check()
        with _lock:
            if _info.get("state") == "checking":
                _info.clear()
                _info.update(result)
    threading.Thread(target=work, daemon=True).start()


def start_apply():
    """Pull and restart. Returns (ok, error message)."""
    with _lock:
        if _info.get("state") != "available":
            return False, "There is nothing to update."
        if _info.get("ahead"):
            return False, "This copy has commits that are not on the remote, so it can't be updated automatically. Update it with git."
        _info["state"] = "applying"
        _info["message"] = "Downloading the update…"
    threading.Thread(target=_apply, daemon=True).start()
    return True, ""


def _fail(message):
    with _lock:
        _info["state"], _info["message"] = "failed", message


def _apply():
    try:
        old = _out("rev-parse", "HEAD")
        pull = _git("pull", "--ff-only", timeout=180)
        if pull.returncode != 0:
            text = (pull.stderr or pull.stdout).strip()
            log.error("git pull failed: %s", text)
            return _fail("git pull failed:\n" + "\n".join(text.splitlines()[-6:]))
        new = _out("rev-parse", "HEAD")
        needs_install = "requirements.txt" in _out("diff", "--name-only", old, new).split()
    except (OSError, subprocess.SubprocessError) as e:
        return _fail(f"git pull failed: {e}")

    if not supervised():
        with _lock:
            _info["state"] = "updated"
            _info["message"] = "Updated. Close BeatThief and start it again to use the new version."
        return
    with _lock:
        _info["state"] = "restarting"
        _info["message"] = "Updated. Restarting BeatThief…"
    time.sleep(1.5)                                          # let the UI see the message
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(RESTART_AND_INSTALL if needs_install else RESTART)


def install_requirements() -> bool:
    """pip install -r requirements.txt with this interpreter. Used by the supervisor while the app is stopped.
    If the full set fails (typically the optional essentia), retry without it."""
    requirements = ROOT / "requirements.txt"

    def pip(path) -> bool:
        return subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(path)], cwd=ROOT).returncode == 0

    if pip(requirements):
        return True
    lines = [ln for ln in requirements.read_text(encoding="utf-8").splitlines() if not ln.lower().startswith("essentia")]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write("\n".join(lines) + "\n")
    try:
        return pip(tmp.name)
    finally:
        os.unlink(tmp.name)
