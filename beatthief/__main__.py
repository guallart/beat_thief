"""Command line entry point:  python -m beatthief {web,download} ..."""
import argparse
import sys

from .downloader import FORMATS, download_audio


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="beatthief", description="Download and tag audio from YouTube and SoundCloud.")
    commands = parser.add_subparsers(dest="command", required=True)

    web = commands.add_parser("web", help="start the web interface")
    web.add_argument("--host", default="127.0.0.1",
                     help="address to listen on; use 0.0.0.0 to expose it on your network (there is no login!)")
    web.add_argument("--port", type=int, default=8000)

    download = commands.add_parser("download", help="download a track or a playlist from the terminal")
    download.add_argument("url")
    download.add_argument("--format", choices=list(FORMATS), help="default: the format chosen in Settings (wav at first)")
    download.add_argument("--playlist", action="store_true", help="download the whole playlist, not just one track")

    args = parser.parse_args(argv)

    if args.command == "web":
        from .web.app import run
        run(args.host, args.port)
        return 0

    ok, files = download_audio(args.url, playlist=args.playlist, audio_format=args.format)
    for path in files:
        print(path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
