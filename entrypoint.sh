#!/bin/bash
set -e

echo "[headless] Starting Xvfb on display :99..."
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!

# Wait for Xvfb to be ready
for i in {1..30}; do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "[headless] Xvfb is ready."
        break
    fi
    sleep 0.1
done

# Ensure /root/Downloads exists and /mnt/music points to it if configured
mkdir -p /root/Downloads
if [ ! -d /mnt/music ]; then
    mkdir -p /mnt
    ln -sf /root/Downloads /mnt/music
fi

export DISPLAY=:99
export LD_PRELOAD=/app/libbridge.so
export BRIDGE_PORT=8081
export BRIDGE_URL=http://127.0.0.1:8081
export PORT=8080
export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1

# Locate AppImage from mounted volume or local fallback
APPIMAGE=""
if [ -d /app/appimage ]; then
    APPIMAGE=$(find /app/appimage -maxdepth 1 -name "*.AppImage" 2>/dev/null | head -n 1)
fi
if [ -z "$APPIMAGE" ] && [ -d /app ]; then
    APPIMAGE=$(find /app -maxdepth 1 -name "*.AppImage" 2>/dev/null | head -n 1)
fi

# If neither an AppImage nor an existing extracted runtime is found, display clear error
if [ -z "$APPIMAGE" ] && [ ! -f /app/squashfs-root/AppRun ]; then
    echo ""
    echo "========================================================================"
    echo " [ERROR] SpotiFLAC-Next AppImage not found!"
    echo ""
    echo " SpotiFLAC-Next is a supporter build available to donors supporting"
    echo " development (see https://github.com/spotbye/SpotiFLAC-Next)."
    echo ""
    echo " Please place your Linux .AppImage file into the 'appimage/' folder:"
    echo "   ./appimage/SpotiFLAC-Next.AppImage"
    echo ""
    echo " Then start the container:"
    echo "   docker compose up -d"
    echo "========================================================================"
    echo ""
    sleep 5
    exit 1
fi

# Prepare/extract AppImage if present and needed
EXTRACT_FRONTEND=0
if [ -n "$APPIMAGE" ]; then
    APPIMAGE_HASH=$(sha256sum "$APPIMAGE" 2>/dev/null | awk '{print $1}')
    STORED_HASH=""
    if [ -f /app/squashfs-root/.appimage_hash ]; then
        STORED_HASH=$(cat /app/squashfs-root/.appimage_hash 2>/dev/null || true)
    fi

    if [ ! -f /app/squashfs-root/AppRun ] || [ "$APPIMAGE" -nt /app/squashfs-root/AppRun ] || [ "$APPIMAGE_HASH" != "$STORED_HASH" ]; then
        echo "[headless] Detected new or updated AppImage: $(basename "$APPIMAGE")..."
        echo "[headless] Extracting SpotiFLAC-Next runtime..."
        rm -rf /app/squashfs-root
        cp "$APPIMAGE" /tmp/spoti.AppImage
        chmod +x /tmp/spoti.AppImage
        cd /app && /tmp/spoti.AppImage --appimage-extract >/dev/null 2>&1
        rm -f /tmp/spoti.AppImage
        echo "$APPIMAGE_HASH" > /app/squashfs-root/.appimage_hash
        echo "[headless] SpotiFLAC-Next runtime ready."
        EXTRACT_FRONTEND=1
    fi
fi

# Prepare official React web frontend from binary if updated or missing
if [ -f /app/squashfs-root/usr/bin/SpotiFLAC-Next ] && [ -f /app/extract_frontend.py ]; then
    if [ "$EXTRACT_FRONTEND" = "1" ] || [ ! -d /app/web/assets ] || [ ! -f /app/web/index.html ]; then
        echo "[headless] Updating official React web frontend assets..."
        python3 /app/extract_frontend.py /app/squashfs-root/usr/bin/SpotiFLAC-Next /app/web /app/wails-browser-shim.js || true
    fi
fi

echo "[headless] Launching SpotiFLAC-Next runtime..."
/app/squashfs-root/AppRun &
APP_PID=$!

# Cleanup trap
cleanup() {
    echo "[headless] Shutting down services..."
    kill $APP_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "[headless] Waiting for WebKit bridge on port 8081..."
for i in {1..60}; do
    if curl -s http://127.0.0.1:8081/health | grep -q "ok"; then
        echo "[headless] SpotiFLAC WebKit bridge is connected!"
        break
    fi
    sleep 0.5
done

echo "[headless] Starting SpotiFLAC Web Server on http://0.0.0.0:8080..."
python3 /app/web_server.py &
WEB_PID=$!

# Keep running
wait $WEB_PID
