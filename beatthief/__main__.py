"""Command line entry point:  python -m beatthief {web,download} ..."""
import argparse
import os
import subprocess
import sys

from . import updater
from .downloader import FORMATS, download_audio


def _wait(proc):
    """Wait for the child. Ctrl+C reaches the child too, so after one we just give it a moment to exit."""
    interrupted = False
    while True:
        try:
            return proc.wait(timeout=5 if interrupted else None), interrupted
        except KeyboardInterrupt:
            interrupted = True
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait(), interrupted


def supervise(argv) -> int:
    """Run the web interface as a child process and start it again when it asks for a restart
    (after an update). The supervisor itself only uses the standard library, so it can reinstall
    the requirements while the app is stopped."""
    env = {**os.environ, "BEATTHIEF_SUPERVISED": "1"}
    while True:
        proc = subprocess.Popen([sys.executable, "-m", "beatthief", *argv], cwd=updater.ROOT, env=env)
        code, interrupted = _wait(proc)
        if interrupted or code not in (updater.RESTART, updater.RESTART_AND_INSTALL):
            return code if code >= 0 else 1
        if code == updater.RESTART_AND_INSTALL:
            print("\nBeatThief was updated: installing the new requirements...", flush=True)
            if not updater.install_requirements():
                print("Could not install every requirement; starting anyway.", flush=True)
        print("Restarting BeatThief...\n", flush=True)
        env["BEATTHIEF_RESTARTED"] = "1"      # the open page reloads itself: don't open another tab


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(prog="beatthief", description="Download and tag audio from YouTube and SoundCloud.")
    commands = parser.add_subparsers(dest="command", required=True)

    web = commands.add_parser("web", help="start the web interface")
    web.add_argument("--host", default="127.0.0.1",
                     help="address to listen on; use 0.0.0.0 to expose it on your network (there is no login!)")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")

    download = commands.add_parser("download", help="download a track or a playlist from the terminal")
    download.add_argument("url")
    download.add_argument("--format", choices=list(FORMATS), help="default: the format chosen in Settings (wav at first)")
    download.add_argument("--playlist", action="store_true", help="download the whole playlist, not just one track")

    args = parser.parse_args(argv)

    if args.command == "web":
        if not updater.supervised():
            return supervise(argv)
        from .web.app import run
        run(args.host, args.port, open_browser=not args.no_browser)
        return 0

    ok, files = download_audio(args.url, playlist=args.playlist, audio_format=args.format)
    for path in files:
        print(path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
