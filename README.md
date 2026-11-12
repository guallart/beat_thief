# BeatThief

BeatThief downloads audio from YouTube and SoundCloud, converts it to WAV (or FLAC, MP3, M4A), fills in the tags and gives you a small library to tidy everything up afterwards. It has a local web interface and a command line, both built on top of [yt-dlp](https://github.com/yt-dlp/yt-dlp).

![Download tab](docs/screenshots/download.png)

## Features

- **Download** — paste one or more links, pick a format and go. YouTube and SoundCloud each have their own rate limit, so extra links wait in the queue instead of getting your IP blocked.
- **Library** — every audio file in the download folder in an editable table: tags, cover art, BPM and key detection, filters, an audio player with waveform and a three-band equalizer. WAV, AIFF, MP3, FLAC, OGG/Opus and M4A are supported.
- **Convert** — drop audio files and convert them to another format with FFmpeg. Tags are kept and the result lands in the library.
- **Settings** — download folder, default format, rate limits and theme. Saved in `config.json`, shared with the command line.

![Library tab](docs/screenshots/library.png)

## Installing

### Quick install

The install scripts check for Python 3.10+ and FFmpeg (installing them with your package manager or winget if missing), create the `.venv` and install the dependencies. They also write a small launcher.

```bash
./install.sh          # Linux (macOS with Homebrew also works)
./run.sh              # starts the web interface at http://127.0.0.1:8000
```

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1     # Windows
.\run.bat            # starts the web interface at http://127.0.0.1:8000
```

Both launchers pass their arguments on, so `./run.sh download "<url>" --format flac` works too.

### Manual install

You need Python 3.10 or newer and FFmpeg (on your `PATH`). Everything Python-related goes into a virtual environment in `.venv`, so nothing is installed system-wide and deleting that folder removes it.

**Linux**

```bash
sudo apt install python3 python3-venv ffmpeg    # Debian, Ubuntu
sudo dnf install python3 ffmpeg                 # Fedora
sudo pacman -S python ffmpeg                    # Arch

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Windows**

Install Python from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*), then FFmpeg with winget. Open a new terminal afterwards so it picks up the `PATH`.

```powershell
winget install Gyan.FFmpeg

py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or skip activation and call the venv's Python directly (`.venv\Scripts\python -m beatthief web`).

BPM and key detection uses [essentia](https://essentia.upf.edu/) when available. It has no Windows build, so there BeatThief falls back to a built-in estimator that only needs numpy: the BPM is practically the same, the key is less reliable. Results are always shown as pending edits before anything is written.

## Running it

Run these from the project folder, with the venv activated or by calling the Python inside `.venv` directly.

```bash
python -m beatthief web                                            # web interface
python -m beatthief download "https://soundcloud.com/..." --format flac
python -m beatthief download "https://www.youtube.com/playlist?list=..." --playlist
```

The web interface opens <http://127.0.0.1:8000> in your browser (`--no-browser` or `BEATTHIEF_NO_BROWSER=1` to prevent it, `--port` to change the port). It only listens on your own machine; `--host 0.0.0.0` exposes it to your network, but there is no login, so only do that on a network you trust. Download formats are `wav` (default), `flac`, `mp3` and `m4a`.

## Updates

If you cloned the repository, the web interface checks the git remote when it starts and offers to update when there are new commits. **Update and restart** runs `git pull --ff-only` (it never overwrites your local changes) and restarts BeatThief, installing new requirements if needed. It needs the `git` command. Set `BEATTHIEF_NO_UPDATE_CHECK=1` to switch the check off.

## Good to know

- YouTube and SoundCloud serve compressed audio. Converting to WAV or FLAC doesn't bring back any quality, it just stores what was there. If sound quality matters, buy the track.
- The default rate limit is 20 downloads every 10 minutes per platform, with 5 to 10 seconds between them. You can change it in Settings.
- Downloading from these sites may go against their terms of use and, depending on the track, copyright law. Use it for things you're allowed to download.
