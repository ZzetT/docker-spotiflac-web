# docker-spotiflac-web (Web UI + Docker for SpotiFLAC-Next)

A lightweight Docker container and modern Web UI wrapper for **SpotiFLAC-Next**. Run your supporter build of SpotiFLAC-Next headlessly on a home server or NAS without a physical display or desktop environment.

> [!IMPORTANT]
> **Pure Wrapper Notice**: This repository contains **NO** downloading, audio ripping, DRM-bypassing, or scraping code. It is exclusively an open-source headless display wrapper, internal IPC bridge, and HTML Web UI. All searches, metadata retrieval, and downloads are performed entirely by the official, user-supplied **SpotiFLAC-Next** binary.

---

## Architecture: How It Works

```
┌──────────────────────────────────────────────────────────────┐
│  Browser / Client (Web UI at http://<server-ip>:8085)        │
│  ├── Official SpotiFLAC-Next React/Tailwind/Radix UI         │
│  └── wails-browser-shim.js (Proxies window.go & SSE events)  │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP RPC (:8085) & SSE Stream
┌──────────────────────────────▼───────────────────────────────┐
│  web_server.py (Static asset server & RPC/SSE bridge)        │
└──────────────────────────────┬───────────────────────────────┘
                               │ Internal Bridge (:8081)
┌──────────────────────────────▼───────────────────────────────┐
│  libbridge.so (C hook intercepting WebKitGTK WebView)        │
└──────────────────────────────┬───────────────────────────────┘
                               │ Wails Go Bindings
┌──────────────────────────────▼───────────────────────────────┐
│  SpotiFLAC-Next (User-supplied AppImage running in Xvfb)    │
│  └── Performs all searches, metadata fetching & FLAC saving  │
└──────────────────────────────────────────────────────────────┘
```

1. **Virtual Display**: SpotiFLAC-Next is a desktop GUI application built with Wails (Go + WebKitGTK). This container runs `Xvfb` (X Virtual Framebuffer) to provide an invisible virtual display so the application runs headlessly on any Linux server.
2. **Official React UI Extraction**: On startup, `extract_frontend.py` extracts the authentic React/Tailwind/Radix production single-page application directly from the Go binary's `embed.FS` table.
3. **Browser Compatibility Shim (`wails-browser-shim.js`)**: A client-side adapter emulates `window.go` via a dynamic JavaScript Proxy and `window.runtime` via Server-Sent Events (SSE), enabling the official desktop frontend to run in any standard web browser.
4. **WebKit Bridge**: A minimal C library (`bridge.c`) hooks into the GTK WebKit view, translating remote web RPC requests into internal Wails function invocations.
5. **100% Delegated Execution**: All streaming platform communication, search queries, format conversions, and downloads are executed exclusively by the official SpotiFLAC-Next binary provided by the user.

---

## Features (via Headless Web UI)

- 🌐 **Authentic Official React UI**: Enjoy the complete, original SpotiFLAC-Next interface (dark/light themes, animations, queue manager, search tabs, settings, lyrics, audio tools) served directly to mobile, desktop, or tablet browsers.
- ⚡ **Real-Time Event Streaming**: Live download progress, queue changes, and notifications streamed instantly via Server-Sent Events (SSE).
- 🔗 **Direct URL & Album Support**: Paste links from Amazon Music, Spotify, Tidal, Qobuz, or Deezer directly into the web UI.
- 🔍 **Multi-Platform Search**: Search across supported streaming services directly inside the web interface.
- ⬇️ **Full Queue & Download Management**: Track downloads in real time with pause, retry, and cancellation controls.
- 🎛️ **Full Settings Editor**: Configure download directories, naming templates, audio conversion, ReplayGain tagging, and credentials.
- 🔒 **Supporter Session Retention**: Mounts your existing supporter tokens (`~/.spotiflac-next`) so downloads function immediately without re-authenticating.

---

## Prerequisites

**SpotiFLAC-Next** is developed and maintained by [spotbye](https://github.com/spotbye/SpotiFLAC-Next) as a supporter build for project donors (via [coffee.spotbye.qzz.io](https://coffee.spotbye.qzz.io), Patreon, or other supported donation methods).

Because this binary is distributed to supporters, it is **not** included in this repository. You must supply your own Linux AppImage file to run the container.

---

## Directory Overview

```
docker-spotiflac-web/
├── appimage/
│   └── README.md             # Place your SpotiFLAC-Next .AppImage here
├── web/
│   ├── index.html            # Official React single-page application entrypoint
│   └── assets/               # Extracted production React bundles, styles & icons
├── extract_frontend.py       # Extracts embedded React UI from Go binary / AppImage
├── wails-browser-shim.js     # Wails v2 browser adapter (window.go & window.runtime proxy)
├── web_server.py             # Python HTTP server providing Web UI, RPC bridge & SSE
├── bridge.c                  # Lightweight C library intercepting WebKit WebView
├── docker-compose.yml        # Docker Compose service definition
├── Dockerfile                # Headless Ubuntu container with Xvfb and WebKitGTK
├── entrypoint.sh             # Startup, auto-extraction & orchestration script
├── downloads/                # Download destination (mounted into container)
├── .env.example              # Sample environment configuration
└── LICENSE                   # MIT License
```

---

## Quickstart

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/docker-spotiflac-web.git
cd docker-spotiflac-web
```

### 2. Place your AppImage

Copy your downloaded Linux AppImage into the `appimage/` directory:

```bash
cp /path/to/SpotiFLAC-Next.AppImage ./appimage/
```
*(Any filename ending in `.AppImage` in the `appimage/` folder will be detected automatically).*

### 3. Ensure Config Directory Exists

If you haven't run SpotiFLAC desktop on your host before, create the config directory so Docker doesn't initialize it with root-only ownership:

```bash
mkdir -p "${HOME}/.spotiflac-next"
```

### 4. Start the Container

Ensure any local desktop instance of SpotiFLAC is closed (to avoid database locks), then run:

```bash
docker compose up -d --build
```

### 5. Open the Web Interface

Open your web browser and navigate to:
```
http://<your-home-server-ip>:8085
```
*(If running on the same machine, visit `http://localhost:8085`)*

---

## Configuration

Configuration can be customized directly in `docker-compose.yml` or via a `.env` file (see `.env.example`):

- **Host Port**: `HOST_PORT=8085` (default: 8085).
- **Downloads Folder**: By default, music is saved into `./downloads` (mounted to `/root/Downloads` in the container).
- **Supporter Token**: Mounted from `${HOME}/.spotiflac-next` to `/root/.spotiflac-next`.

---

## Disclaimer

This project is an independent headless wrapper and web interface for SpotiFLAC-Next. It is intended strictly for personal and educational purposes. Users are responsible for complying with local copyright laws and the Terms of Service of any third-party streaming platforms used.

---

## License

This wrapper project is licensed under the [MIT License](LICENSE).
SpotiFLAC-Next itself is property of its respective author(s).
