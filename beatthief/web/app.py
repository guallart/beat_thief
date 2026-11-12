"""Local web UI for BeatThief (Flask). Start it with:  python -m beatthief web"""
import os
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from flask import Flask, Response, abort, jsonify, request, send_from_directory

from .. import log as logging_setup
from ..log import clean_error, log, verbose
from .. import analysis, config, converter, downloader, tagging, throttle, updater, waveform
from ..sources import is_playlist, validate_and_clean_url

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__, static_folder=None)
# One worker per platform: each platform has its own rate limit and cooldown,
# so a cooldown on one doesn't block the other.
executors = {
    "youtube": ThreadPoolExecutor(max_workers=1),
    "soundcloud": ThreadPoolExecutor(max_workers=1),
}

jobs = {}  # id -> job dict
FINISHED = ("done", "error", "cancelled")
DUPLICATE = "Already in the queue (dismiss it there to add it again)."
jobs_lock = threading.Lock()


def _update(job_id, run, **fields):
    """Change a job. Cancelled, dismissed or restarted (retried) jobs are left alone by the old run."""
    with jobs_lock:
        job = jobs.get(job_id)
        if job is not None and job["run"] == run and job["status"] != "cancelled":
            job.update(fields)


def _is_stale(job_id, run):
    with jobs_lock:
        job = jobs.get(job_id)
        return job is None or job["run"] != run or job["status"] == "cancelled"


def _check_cancelled(job_id, run):
    if _is_stale(job_id, run):
        raise yt_dlp.utils.DownloadCancelled("Cancelled.")  # stops yt-dlp at its next progress update


def _run_job(job_id, run):
    try:
        _check_cancelled(job_id, run)  # cancelled while it was still waiting in the queue
    except yt_dlp.utils.DownloadCancelled:
        return
    with jobs_lock:
        url, audio_format = jobs[job_id]["url"], jobs[job_id]["format"]
    log.info("Job %s started: %s (%s)", job_id, url, audio_format)
    _update(job_id, run, status="downloading", progress=0)

    def on_wait(seconds_left):
        _check_cancelled(job_id, run)
        if seconds_left > 0:
            _update(job_id, run, status="cooldown", cooldown=round(seconds_left))
        else:
            _update(job_id, run, status="downloading", cooldown=0)

    def on_progress(stage, pct, title):
        _check_cancelled(job_id, run)
        fields = {"status": stage}
        if pct is not None:
            fields["progress"] = round(pct, 1)
        if title:
            fields["title"] = title
        _update(job_id, run, **fields)

    try:
        paths = downloader.fetch_audio(url, on_progress=on_progress, on_wait=on_wait,
                                          audio_format=audio_format)
        log.info("Job %s finished: %s", job_id, ", ".join(os.path.basename(p) for p in paths))
        if _is_stale(job_id, run):  # cancelled while converting: that step can't be interrupted, so remove its result
            for p in paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            return
        _update(job_id, run, status="done", progress=100,
                files=[os.path.basename(p) for p in paths],
                title=os.path.splitext(os.path.basename(paths[0]))[0])
    except Exception as e:
        message = clean_error(e, "Download failed.")
        if isinstance(e, yt_dlp.utils.DownloadCancelled):
            log.info("Job %s cancelled", job_id)
        else:
            log.error("Job %s failed (%s): %s", job_id, url, message, exc_info=verbose())
        _update(job_id, run, status="error", error=message)


MAX_FILES = 5000
tag_write_lock = threading.Lock()


def _base_dir():
    return config.get_download_dir().resolve()


def _safe_file(rel):
    """Resolve a library-relative path, refusing anything outside the library folder."""
    base = _base_dir()
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file() or not tagging.is_supported(path):
        abort(404)
    return base, path


def _file_entry(base, path):
    st = path.stat()
    info = tagging.read_tags(path)
    return {
        "path": path.relative_to(base).as_posix(), "name": path.name, "size": st.st_size,
        "modified": st.st_mtime, "duration": info["duration"], "has_cover": info["has_cover"],
        "tags": info["tags"],
    }


def _list_files():
    """All supported audio files under the library folder (recursive, hidden dirs skipped)."""
    base = _base_dir()
    if not base.is_dir():
        return []
    items = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith(".") or not tagging.is_supported(name):
                continue
            try:
                items.append(_file_entry(base, (Path(root) / name)))
            except Exception:
                continue  # unreadable or corrupt file: skip it instead of breaking the list
            if len(items) >= MAX_FILES:
                return items
    return sorted(items, key=lambda i: i["modified"], reverse=True)


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


def _enqueue(url, source, audio_format, title=None):
    """Add a job and return its id, or None if the same link is already in the queue (waiting, running or
    finished and not dismissed yet). Failed and cancelled ones don't count, so they can be retried."""
    job_id = uuid.uuid4().hex[:10]
    with jobs_lock:
        if any(j["url"] == url and j["status"] not in ("error", "cancelled") for j in jobs.values()):
            return None
        jobs[job_id] = {
            "id": job_id, "url": url, "source": source, "format": audio_format, "status": "queued",
            "progress": 0, "cooldown": 0, "title": title, "files": [], "error": None,
            "created": time.time(), "run": 0,
        }
    executors[source].submit(_run_job, job_id, 0)
    return job_id


@app.post("/api/jobs")
def create_jobs():
    """Body: {"urls": [...], "format": ...}. A playlist link is split into one job per track; they run
    one after another through the rate limiter, exactly like tracks pasted one by one."""
    data = request.get_json(silent=True) or {}
    audio_format = data.get("format") or downloader.default_format()
    if audio_format not in downloader.FORMATS:
        return jsonify({"error": "Unsupported format."}), 400
    results = []
    for raw in data.get("urls", []):
        ok, value, source, _ = validate_and_clean_url(str(raw))
        if not ok:
            results.append({"input": raw, "error": value})
            continue
        if not is_playlist(value):
            job_id = _enqueue(value, source, audio_format)
            results.append({"input": raw, "id": job_id} if job_id else {"input": raw, "error": DUPLICATE})
            continue
        try:
            tracks = downloader.list_playlist(value, source)
        except Exception as e:
            log.error("Could not read the playlist %s: %s", value, e, exc_info=verbose())
            results.append({"input": raw, "error": clean_error(e, "Could not read the playlist.")})
            continue
        if not tracks:
            results.append({"input": raw, "error": "The playlist is empty or not available."})
            continue
        ids = [i for i in (_enqueue(t["url"], source, audio_format, t["title"]) for t in tracks) if i]
        if not ids:
            results.append({"input": raw, "error": "All the tracks of this playlist are already in the queue."})
        else:
            results.append({"input": raw, "id": ids[0], "count": len(ids), "skipped": len(tracks) - len(ids)})
    return jsonify(results), 202


@app.get("/api/jobs")
def list_jobs():
    with jobs_lock:
        return jsonify(sorted(jobs.values(), key=lambda j: j["created"], reverse=True))


@app.delete("/api/jobs")
def dismiss_finished_jobs():
    with jobs_lock:
        for job_id in [i for i, j in jobs.items() if j["status"] in FINISHED]:
            del jobs[job_id]
    return "", 204


@app.delete("/api/jobs/<job_id>")
def dismiss_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job and job["status"] in FINISHED:
            del jobs[job_id]
    return "", 204


@app.post("/api/jobs/<job_id>/retry")
def retry_job(job_id):
    """Queue a failed or cancelled job again (unless the same link was added again in the meantime)."""
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job["status"] not in ("error", "cancelled"):
            return "", 204
        if any(j is not job and j["url"] == job["url"] and j["status"] not in ("error", "cancelled") for j in jobs.values()):
            return jsonify({"error": DUPLICATE}), 409
        log.info("Job %s: retry", job_id)
        job["run"] += 1
        job.update(status="queued", progress=0, cooldown=0, error=None)
        run, source = job["run"], job["source"]
    executors[source].submit(_run_job, job_id, run)
    return "", 204


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    """Cancel a job that is queued, waiting, downloading or converting. A running download stops at its
    next progress update (a second at most)."""
    with jobs_lock:
        job = jobs.get(job_id)
        if job and job["status"] not in FINISHED:
            log.info("Job %s: cancel requested", job_id)
            job.update(status="cancelled", cooldown=0)
    return "", 204


def _settings_payload():
    return {
        "download_dir": str(config.get_download_dir()),
        "default_format": downloader.default_format(),
        "formats": list(downloader.FORMATS),
        "rate_limit": throttle.limits(),
        "rate_limit_defaults": throttle.DEFAULTS,
        "verbose": verbose(),
    }


@app.get("/api/settings")
def get_settings():
    return jsonify(_settings_payload())


@app.put("/api/settings")
def update_settings():
    """Body: any of {"download_dir": str, "default_format": str, "rate_limit": {...} | null, "verbose": bool}.
    Everything is validated before anything is saved."""
    data = request.get_json(silent=True) or {}
    try:
        if "default_format" in data and data["default_format"] not in downloader.FORMATS:
            raise ValueError("Unsupported format.")
        limits = throttle.validate_limits(data["rate_limit"]) if data.get("rate_limit") else None
        if "download_dir" in data:
            config.set_download_dir(data["download_dir"])  # may fail: do it before saving the rest
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if "verbose" in data:
        config.set_setting("verbose", bool(data["verbose"]))
        logging_setup.set_verbose(bool(data["verbose"]))
    if "default_format" in data:
        config.set_setting("default_format", data["default_format"])
    if "rate_limit" in data:  # null / empty resets to the defaults
        config.set_setting("rate_limit", limits)
    return jsonify(_settings_payload())


@app.get("/api/convert/formats")
def convert_formats():
    return jsonify({k: label for k, (label, _) in converter.FORMATS.items()})


@app.post("/api/convert")
def convert_file():
    """Multipart form: file + format. Converts into the download folder and returns the new file's path."""
    upload = request.files.get("file")
    audio_format = request.form.get("format", "")
    if not upload or audio_format not in converter.FORMATS:
        return jsonify({"error": "Choose a file and a format."}), 400
    try:
        with tag_write_lock:  # nothing else writes tags to files at the same moment
            path = converter.convert(upload, upload.filename or "audio", audio_format)
    except Exception as e:
        log.error("Conversion of %s to %s failed: %s", upload.filename, audio_format, e, exc_info=verbose())
        return jsonify({"error": str(e) or "Conversion failed."}), 422
    log.info("Converted %s -> %s", upload.filename, path.name)
    return jsonify({"path": path.relative_to(_base_dir()).as_posix(), "name": path.name, "size": path.stat().st_size})


@app.get("/api/files")
def list_files():
    return jsonify(_list_files())


@app.get("/files/<path:rel>")
def get_file(rel):
    base, path = _safe_file(rel)
    return send_from_directory(base, path.relative_to(base).as_posix(), as_attachment=True)


@app.get("/api/stream/<path:rel>")
def stream_file(rel):
    base, path = _safe_file(rel)
    return send_from_directory(base, path.relative_to(base).as_posix())  # inline + Range support


waveform_cache = {}  # (path, mtime, size) -> {"low", "mid", "high"}
waveform_lock = threading.Lock()


@app.get("/api/waveform/<path:rel>")
def get_waveform(rel):
    """Three-band overview of the track, for the player's seek bar."""
    _, path = _safe_file(rel)
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    with waveform_lock:
        cached = waveform_cache.get(key)
    if cached is None:
        try:
            cached = waveform.compute(path)
        except Exception as e:
            log.warning("Waveform of %s failed: %s", rel, e, exc_info=verbose())
            return jsonify({"error": str(e) or "Could not analyse the file."}), 422
        with waveform_lock:
            if len(waveform_cache) > 50:
                waveform_cache.clear()
            waveform_cache[key] = cached
    return jsonify(cached)


@app.delete("/api/files/<path:rel>")
def delete_file(rel):
    _, path = _safe_file(rel)
    path.unlink()
    return "", 204


@app.get("/api/cover/<path:rel>")
def get_cover(rel):
    _, path = _safe_file(rel)
    cover = tagging.read_cover(path)
    if not cover:
        abort(404)
    mime, data = cover
    return Response(data, mimetype=mime, headers={"Cache-Control": "private, max-age=3600"})


@app.put("/api/tags")
def update_tags():
    """Body: {"files": [{"path": "...", "tags": {field: value, ...}}, ...]}"""
    data = request.get_json(silent=True) or {}
    results = []
    for item in data.get("files", []):
        rel = str(item.get("path", ""))
        tags = {k: v for k, v in (item.get("tags") or {}).items() if k in tagging.FIELDS}
        try:
            base, path = _safe_file(rel)
            with tag_write_lock:
                tagging.write_tags(path, tags)
            results.append({"path": rel, "ok": True, **_file_entry(base, path)})
        except Exception as e:
            reason = "File not found." if getattr(e, "code", None) == 404 else (str(e) or "Could not write tags.")
            log.warning("Writing tags to %s failed: %s", rel, reason, exc_info=verbose())
            results.append({"path": rel, "ok": False, "error": reason})
    return jsonify(results)


# BPM / key analysis: results are returned to the UI (which shows them as pending edits);
# nothing is written to the files here.
analysis_executor = ThreadPoolExecutor(max_workers=1)  # CPU-heavy: one at a time
analyses = {}  # path -> {"status": queued|running|done|error, "bpm", "key", "error"}
analyses_lock = threading.Lock()


def _run_analysis(rel):
    with analyses_lock:
        analyses[rel]["status"] = "running"
    try:
        _, path = _safe_file(rel)
        result = analysis.analyze(path)
        outcome = {"status": "done", **result}
    except Exception as e:
        reason = "File not found." if getattr(e, "code", None) == 404 else (str(e) or "Analysis failed.")
        log.warning("Analysis of %s failed: %s", rel, reason, exc_info=verbose())
        outcome = {"status": "error", "error": reason}
    with analyses_lock:
        analyses[rel] = outcome


@app.post("/api/analyze")
def start_analysis():
    """Body: {"paths": ["rel/path.wav", ...]}"""
    data = request.get_json(silent=True) or {}
    for rel in [str(p) for p in data.get("paths", [])][:MAX_FILES]:
        with analyses_lock:
            if analyses.get(rel, {}).get("status") in ("queued", "running"):
                continue
            analyses[rel] = {"status": "queued"}
        analysis_executor.submit(_run_analysis, rel)
    return "", 202


@app.get("/api/analyze")
def get_analysis():
    """Current state of every analysis. Finished ones are returned once and then forgotten."""
    with analyses_lock:
        snapshot = {rel: dict(state) for rel, state in analyses.items()}
        for rel, state in snapshot.items():
            if state["status"] in ("done", "error"):
                del analyses[rel]
    return jsonify(snapshot)


# Updates from the git remote (see updater.py). The check starts with the server; the UI polls this.
@app.get("/api/update")
def update_status():
    with jobs_lock:
        busy = sum(1 for j in jobs.values() if j["status"] in ("queued", "downloading", "cooldown"))
    return jsonify({**updater.get_state(), "boot": updater.BOOT_ID, "busy": busy, "can_restart": updater.supervised()})


@app.post("/api/update/check")
def update_check():
    updater.start_check()
    return "", 202


@app.post("/api/update/apply")
def update_apply():
    ok, error = updater.start_apply()
    return ("", 202) if ok else (jsonify({"error": error}), 409)


def _open_browser_when_ready(host, port):
    """Open the UI in the default browser, but only once the server accepts connections (otherwise a
    fast browser would get "connection refused")."""
    # A wildcard address isn't something a browser can visit: use the local one instead.
    visit = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    url = f"http://[{visit}]:{port}" if ":" in visit else f"http://{visit}:{port}"

    def work():
        for _ in range(100):                                   # wait up to ~10 s for the server
            try:
                socket.create_connection((visit, port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            return
        try:
            webbrowser.open(url, new=2)                        # a new tab; harmless if there is no browser/display
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


def run(host="127.0.0.1", port=8000, open_browser=True):
    print(f"Saving to {config.get_download_dir()}")
    print(f"BeatThief running at http://{host}:{port}")
    updater.start_check()
    # Not after an automatic restart (the page reloads itself) nor when switched off.
    if open_browser and not os.environ.get("BEATTHIEF_NO_BROWSER") and not os.environ.get("BEATTHIEF_RESTARTED"):
        _open_browser_when_ready(host, port)
    app.run(host=host, port=port, threaded=True)
