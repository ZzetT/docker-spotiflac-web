# docker-spotiflac-web (Web UI + Docker for SpotiFLAC-Next)

A lightweight Docker container and modern Web UI wrapper for **SpotiFLAC-Next**. Run your supporter build of SpotiFLAC-Next headlessly on a home server or NAS without a physical display or desktop environment.

> [!IMPORTANT]
> **Pure Wrapper Notice**: This repository contains **NO** downloading, audio ripping, DRM-bypassing, or scraping code. It is exclusively an open-source headless display wrapper, internal IPC bridge, and HTML Web UI. All searches, metadata retrieval, and downloads are performed entirely by the official, user-supplied **SpotiFLAC-Next** binary.

---

## Architecture: How It Works

```
┌──────────────────────────────────────────────────────────────┐
│  Browser / Client (Web UI at http://<server-ip>:8080)        │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP / REST
┌──────────────────────────────▼───────────────────────────────┐
│  web_server.py (Lightweight Web Server & API proxy)          │
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
2. **WebKit Bridge**: A minimal C library (`bridge.c`) hooks into the GTK WebKit view, translating web API requests into internal Wails function invocations.
3. **Responsive Web UI**: A clean, single-page web interface (`web/index.html`) served by `web_server.py` allows managing the desktop app remotely from any browser on your home network.
4. **100% Delegated Execution**: All streaming platform communication, search queries, format conversions, and downloads are executed exclusively by the official SpotiFLAC-Next binary provided by the user.

---

## Features (via Headless Web UI)

- 🌐 **Modern Remote Web Interface**: Access from any browser on your home network (phone, laptop, tablet) at `http://<server-ip>:8080`.
- 🔗 **Direct URL Paste Support**: Paste album or track links from Amazon Music, Spotify, Tidal, Qobuz, or Deezer directly into the web UI.
- 🔍 **Multi-Platform Search UI**: Trigger multi-service searches in SpotiFLAC-Next directly from the web interface.
- ⬇️ **1-Click Full Album Download**: Remote inspection of tracklists with one-click full album download triggers.
- 📋 **Live Queue & Progress**: Monitor the desktop application's download queue and task states in real time.
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
│   └── index.html            # Responsive, dark-mode Web UI
├── web_server.py             # Python HTTP server providing Web UI & REST API
├── bridge.c                  # Lightweight C library intercepting WebKit WebView
├── docker-compose.yml        # Docker Compose service definition
├── Dockerfile                # Headless Ubuntu container with Xvfb and WebKitGTK
├── entrypoint.sh             # Startup & auto-extraction script
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

Ensure any local desktop instance of SpotiFLAC is closed (to avoid SQLite database locks), then run:

```bash
docker compose up -d --build
```

### 5. Open the Web Interface

Open your web browser and navigate to:
```
http://<your-home-server-ip>:8080
```
*(If running on the same machine, visit `http://localhost:8080`)*

---

## Usage

1. **Paste an Album Link**:
   - Copy an album URL from Amazon Music (e.g., `https://music.amazon.com/albums/...`), Spotify, or Tidal.
   - Paste it into the search bar and click **Fetch / Search**.
   - Review the tracklist and click **⬇️ Download Entire Album (FLAC)** or download individual tracks.
2. **Search by Keyword**:
   - Type an artist or album name (e.g., `Daft Punk Discovery`).
   - Select the platform and click **Search**.
   - Inspect albums from search results and queue them for download.

Downloads are saved directly to `./downloads/` on the host.

---

## Configuration

Configuration can be customized directly in `docker-compose.yml` or via a `.env` file (see `.env.example`):

- **Downloads Folder**: By default, music is saved into `./downloads` (mounted to `/root/Downloads` in the container).
- **Supporter Token**: Mounted from `${HOME}/.spotiflac-next` to `/root/.spotiflac-next`.
- **Port**: Set `PORT=8080` to customize the external port.

---

## Disclaimer

This project is an independent headless wrapper and web interface for SpotiFLAC-Next. It is intended strictly for personal and educational purposes. Users are responsible for complying with local copyright laws and the Terms of Service of any third-party streaming platforms used.

---

## License

This wrapper project is licensed under the [MIT License](LICENSE).
SpotiFLAC-Next itself is property of its respective author(s).
