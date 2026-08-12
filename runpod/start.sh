#!/usr/bin/env bash
#
# Bring up a virtual display, publish it over noVNC, and run the app on it.
#
# Order matters: X must be answering before the window manager, the window
# manager before the app, or the app opens onto a display with no decorations
# and no way to move a dialog off the top-left corner.

set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/workspace/comic-translate}"
RESOLUTION="${RESOLUTION:-1920x1080}"
APP_DIR="${APP_DIR:-/opt/comic-translate}"

# Everything the app writes is redirected onto the pod's volume, so models,
# settings, API keys, glossaries and projects all survive the pod being stopped
# and started again. modules/utils/paths.py reads XDG_DATA_HOME and QSettings
# reads XDG_CONFIG_HOME, so no symlinking is needed — just point them here.
export HOME="$DATA_ROOT/home"
export XDG_DATA_HOME="$DATA_ROOT/data"        # -> data/ComicTranslate/models
export XDG_CONFIG_HOME="$DATA_ROOT/config"    # -> config/ComicLabs/ComicTranslate.conf
export XDG_CACHE_HOME="$DATA_ROOT/cache"
export HF_HOME="$DATA_ROOT/huggingface"       # PaddleOCR-VL checkpoints
mkdir -p "$HOME/Documents" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" \
         "$XDG_CACHE_HOME" "$HF_HOME" "$DATA_ROOT/comics"

# A RunPod proxy URL is only as secret as the pod id, and this one opens a
# desktop with your API keys on it. It always gets a password.
if [ -z "${VNC_PASSWORD:-}" ]; then
    VNC_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 12)"
    echo "================================================================"
    echo "  VNC_PASSWORD was not set, so one was generated for this boot:"
    echo ""
    echo "      $VNC_PASSWORD"
    echo ""
    echo "  It changes every restart. Set VNC_PASSWORD in the pod's"
    echo "  environment variables to pick your own and keep it."
    echo "================================================================"
fi
x11vnc -storepasswd "$VNC_PASSWORD" "$DATA_ROOT/.vncpass" >/dev/null 2>&1
unset VNC_PASSWORD

# Maximise real windows. Without this the app opens at its saved size in the
# corner of a 1920x1080 black rectangle, which looks broken on first run.
# Dialogs and popups are left alone — type="normal" excludes them.
cat > "$DATA_ROOT/openbox-rc.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <applications>
    <application class="*" type="normal">
      <maximized>yes</maximized>
    </application>
  </applications>
</openbox_config>
XML

cleanup() { trap - EXIT INT TERM; kill 0; }
trap cleanup EXIT INT TERM

echo "[start] Xvfb on :1 at ${RESOLUTION}"
Xvfb :1 -screen 0 "${RESOLUTION}x24" -nolisten tcp +extension RANDR &

for _ in $(seq 1 60); do
    if xdpyinfo -display :1 >/dev/null 2>&1; then break; fi
    sleep 0.25
done
if ! xdpyinfo -display :1 >/dev/null 2>&1; then
    echo "[start] Xvfb did not come up" >&2
    exit 1
fi

echo "[start] openbox"
openbox --config-file "$DATA_ROOT/openbox-rc.xml" &

echo "[start] x11vnc on 5900"
x11vnc -display :1 -rfbauth "$DATA_ROOT/.vncpass" -rfbport 5900 \
       -forever -shared -noxdamage -xkb -quiet &

# --heartbeat keeps the websocket alive through RunPod's proxy while you are
# reading a page rather than clicking on one.
echo "[start] noVNC on 6080"
websockify --web=/usr/share/novnc --heartbeat=30 6080 localhost:5900 &

cd "$APP_DIR"
echo "[start] comic.py"
while true; do
    python comic.py || echo "[start] app exited with $?; restarting in 3s"
    sleep 3
done
