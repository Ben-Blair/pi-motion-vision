#!/bin/bash
#
# Symlink live Motion paths to this repo so everything under the project tree
# (etc/motion/, usr/local/bin/, scripts/) is what the system actually uses.
# Edit in Cursor; no nano or deploy_to_live copy step for day-to-day work.
#
# Run once on the Pi:
#   cd ~/pi-motion-vision
#   ./scripts/link_repo_to_live.sh
#
# After changing *.conf:  sudo systemctl restart motion
#
# Motion runs as user "motion". If the repo is under your home directory, the
# motion user must be able to traverse to these files, e.g.:
#   chmod o+x "$HOME"
#
# To return to copied files instead of symlinks, run deploy_to_live.sh (it
# replaces symlinks with regular files where it copies).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

link_file() {
  local src="$1"
  local dest="$2"
  if [ ! -f "$src" ]; then
    echo "  Skip (missing source): $src"
    return 0
  fi
  if [ -L "$dest" ] && [ "$(readlink -f "$dest")" = "$(readlink -f "$src")" ]; then
    echo "  Already linked: $dest"
    return 0
  fi
  if [ -L "$dest" ]; then
    echo "  Replacing symlink: $dest"
    sudo rm -f "$dest"
  elif [ -f "$dest" ]; then
    echo "  Backing up file: $dest"
    sudo mv "$dest" "$dest.bak.$(date +%Y%m%d%H%M%S)"
  elif [ -e "$dest" ]; then
    echo "  Skip (not a plain file): $dest"
    return 0
  fi
  sudo ln -s "$src" "$dest"
  echo "  Linked: $dest -> $src"
}

link_scripts_dir() {
  local dest="/etc/motion/scripts"
  if [ -L "$dest" ] && [ "$(readlink -f "$dest")" = "$(readlink -f "$REPO_DIR/scripts")" ]; then
    echo "  Already linked: $dest -> $REPO_DIR/scripts"
    return 0
  fi
  if [ -d "$dest" ] && [ ! -L "$dest" ]; then
    echo "  Backing up directory: $dest"
    sudo mv "$dest" "$dest.bak.$(date +%Y%m%d%H%M%S)"
  elif [ -L "$dest" ] || [ -e "$dest" ]; then
    echo "  Removing old: $dest"
    sudo rm -rf "$dest"
  fi
  sudo ln -sfn "$REPO_DIR/scripts" "$dest"
  echo "  Linked: $dest -> $REPO_DIR/scripts"
}

echo "Linking live system to repo: $REPO_DIR"
echo

echo "Motion config (/etc/motion/*.conf)"
sudo mkdir -p /etc/motion
shopt -s nullglob
for src in "$REPO_DIR"/etc/motion/*.conf; do
  base="$(basename "$src")"
  link_file "$src" "/etc/motion/$base"
done
shopt -u nullglob

echo
echo "Motion hooks (camera points at /usr/local/bin for these)"
link_file "$REPO_DIR/scripts/on_event_start.sh" /usr/local/bin/on_event_start.sh
link_file "$REPO_DIR/scripts/on_event_end_pipeline.sh" /usr/local/bin/on_event_end_pipeline.sh
link_file "$REPO_DIR/scripts/on_movie_end.sh" /usr/local/bin/on_movie_end.sh

echo
echo "Helpers under /usr/local/bin (repo copy)"
sudo mkdir -p /usr/local/bin
shopt -s nullglob
for src in "$REPO_DIR"/usr/local/bin/*; do
  [ -f "$src" ] || continue
  base="$(basename "$src")"
  link_file "$src" "/usr/local/bin/$base"
done
shopt -u nullglob

echo
echo "Mirror /etc/motion/scripts -> repo scripts/"
link_scripts_dir

echo
echo "Done."
echo "  - Config: edit $REPO_DIR/etc/motion/ then: sudo systemctl restart motion"
echo "  - Hooks and bin: edits under $REPO_DIR/scripts/ and $REPO_DIR/usr/local/bin/ are live immediately for the next run."
echo "  - Pipeline uses select_best_snapshot.py from usr/local/bin (not scripts/) — edit the copy under usr/local/bin/ in the repo."
