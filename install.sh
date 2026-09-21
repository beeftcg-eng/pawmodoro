#!/usr/bin/env bash
# Installs Pawmodoro into ~/.local/share/pawmodoro-app using a Python venv.
# With --relaunch it starts Pawmodoro again when done (used by the app's own
# "Update" button, see update_checker.py).
# Safe on Bazzite/immutable systems: never touches system packages, no
# rpm-ostree layering required.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/pawmodoro-app"
DESKTOP_DIR="$HOME/.local/share/applications"

# Pawmodoro keeps running in the system tray after you close its window (by
# design), so an old process can quietly keep running under the OLD code even
# after you reinstall. Make sure nothing stale is still alive first.
# (Matches both an installed run.sh launch and a "python3 main.py" launch
# from an extracted source folder, since argv[0] is always a full path.)
if pgrep -f "pawmodoro-app/venv/bin/python3 main.py" > /dev/null 2>&1 || pgrep -f "pawmodoro/main.py" > /dev/null 2>&1; then
    echo "Pawmodoro is currently running (likely minimized to the tray)."
    echo "Stopping it so the update actually takes effect..."
    pkill -f "pawmodoro-app/venv/bin/python3 main.py" 2>/dev/null || true
    pkill -f "pawmodoro/main.py" 2>/dev/null || true
    sleep 1
fi

echo "Installing Pawmodoro to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

# Wipe old app files (but keep the venv, so we don't reinstall deps every time)
find "$INSTALL_DIR" -maxdepth 1 -name "*.py" -delete
rm -rf "$INSTALL_DIR/resources"

cp -r "$SRC_DIR"/*.py "$INSTALL_DIR"/
cp -r "$SRC_DIR"/resources "$INSTALL_DIR"/

if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install -r "$SRC_DIR/requirements.txt" --quiet

cat > "$INSTALL_DIR/run.sh" <<EOF
#!/usr/bin/env bash
cd "$INSTALL_DIR"
exec "$INSTALL_DIR/venv/bin/python3" main.py
EOF
chmod +x "$INSTALL_DIR/run.sh"

mkdir -p "$DESKTOP_DIR"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__ICON_PATH__|$INSTALL_DIR/resources/icon.png|g" \
    "$SRC_DIR/pawmodoro.desktop" > "$DESKTOP_DIR/pawmodoro.desktop"
chmod +x "$DESKTOP_DIR/pawmodoro.desktop"

echo ""
echo "Done. Pawmodoro $(cat "$SRC_DIR/version.py" | cut -d'"' -f2) installed."
echo "It should show up in your KDE app launcher (search 'Pawmodoro')."
echo "You can also run it directly with: $INSTALL_DIR/run.sh"

if ! command -v playerctl > /dev/null 2>&1; then
    echo ""
    echo "Note: 'playerctl' isn't installed, so the persistent music player"
    echo "controls won't work yet (the app still runs fine without it)."
    echo "playerctl isn't available via Homebrew. On Bazzite, install it via:"
    echo "  rpm-ostree install playerctl   # layers onto the OS image, needs a reboot"
    echo "or, to avoid a reboot, using distrobox (comes with Bazzite):"
    echo "  distrobox create -n tools -i fedora:latest"
    echo "  distrobox enter tools -- sudo dnf install -y playerctl"
    echo "  distrobox enter tools -- distrobox-export --bin /usr/bin/playerctl"
    echo "  (this puts a playerctl launcher on your host PATH, usually in ~/.local/bin)"
fi
echo ""
echo "Optional: to autostart it on login, run:"
echo "  mkdir -p ~/.config/autostart && cp $DESKTOP_DIR/pawmodoro.desktop ~/.config/autostart/"

if [ "${1:-}" = "--relaunch" ]; then
    echo ""
    echo "Restarting Pawmodoro..."
    nohup "$INSTALL_DIR/run.sh" > /dev/null 2>&1 &
fi
