#!/usr/bin/env bash
# BeatThief installer for Linux (and macOS with Homebrew).
# Creates .venv, installs the Python dependencies, makes sure FFmpeg is present
# and writes a ./run.sh launcher. Safe to run again.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

pkg_install() {  # pkg_install <apt> <dnf> <pacman> <zypper> <brew> ; empty = skip
  if   command -v apt-get >/dev/null; then $SUDO apt-get update && $SUDO apt-get install -y $1
  elif command -v dnf     >/dev/null; then $SUDO dnf install -y $2
  elif command -v pacman  >/dev/null; then $SUDO pacman -S --needed --noconfirm $3
  elif command -v zypper  >/dev/null; then $SUDO zypper install -y $4
  elif command -v brew    >/dev/null; then brew install $5
  else return 1; fi
}

# --- Python (3.10+) ---------------------------------------------------------
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  say "Python 3.10+ not found, installing it"
  pkg_install "python3 python3-venv python3-pip" "python3 python3-pip" "python python-pip" "python3 python3-pip" "python" \
    || fail "Install Python 3.10 or newer manually and run this script again."
  PY=python3
fi
# Debian/Ubuntu ship venv as a separate package
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  say "Installing python3-venv"
  pkg_install "python3-venv" "" "" "" "" || fail "Python's venv module is missing; install it for your distribution."
fi

# --- FFmpeg -----------------------------------------------------------------
if ! command -v ffmpeg >/dev/null; then
  say "FFmpeg not found, installing it"
  pkg_install "ffmpeg" "ffmpeg" "ffmpeg" "ffmpeg" "ffmpeg" \
    || fail "Install FFmpeg manually (it must be on your PATH) and run this script again."
  command -v ffmpeg >/dev/null || fail "FFmpeg is still not available. On Fedora you may need RPM Fusion: https://rpmfusion.org"
fi

# --- virtualenv + dependencies ---------------------------------------------
say "Creating virtual environment in .venv"
"$PY" -m venv .venv
say "Installing Python dependencies"
.venv/bin/python -m pip install --upgrade pip >/dev/null
if ! .venv/bin/python -m pip install -r requirements.txt; then
  say "Full install failed; retrying without the optional BPM/key analysis (essentia)"
  grep -vi '^essentia' requirements.txt > .venv/requirements-noessentia.txt
  .venv/bin/python -m pip install -r .venv/requirements-noessentia.txt \
    || fail "Could not install the dependencies."
  echo "Note: BPM/key detection is unavailable on this system."
fi

# --- launcher ---------------------------------------------------------------
cat > run.sh <<'LAUNCH'
#!/usr/bin/env bash
cd "$(dirname "$(readlink -f "$0")")"
exec .venv/bin/python -m beatthief "${@:-web}"
LAUNCH
chmod +x run.sh

say "Done."
echo
echo "Start the web interface with:   ./run.sh              (then open http://127.0.0.1:8000)"
echo "Or use the command line:        ./run.sh download \"<url>\" --format flac"
