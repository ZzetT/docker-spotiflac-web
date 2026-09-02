# SpotiFLAC-Next Headless (Web UI + Docker)

Run **SpotiFLAC-Next** on your home server in a headless Docker container with a **modern HTML Web Interface**. Search for music or simply copy & paste album links from **Amazon Music**, **Spotify**, **Tidal**, **Qobuz**, or **Deezer** to download lossless FLAC music directly to your server.

---

## Features

- 🌐 **Modern Web Interface**: Access from any browser on your home network (phone, laptop, tablet) at `http://<server-ip>:8080`.
- 🔗 **Direct URL Paste Support**: Copy & paste album or track links directly from:
  - **Amazon Music**: `https://music.amazon.com/albums/...` or `https://music.amazon.com/tracks/...`
  - **Spotify**: `https://open.spotify.com/album/...` or `https://open.spotify.com/track/...`
  - **Tidal / Qobuz / Deezer**: `https://tidal.com/album/...`, `https://www.qobuz.com/album/...`, etc.
- 🔍 **Multi-Platform Search**: Search directly across Amazon Music, Spotify, Tidal, Qobuz, and Deezer from the search bar.
- ⬇️ **1-Click Full Album Download**: View full tracklists with durations and download the whole album in lossless FLAC with a single click.
- 📋 **Live Queue & Progress**: Track ongoing downloads and see download statuses update in real time.
- 🔒 **Supporter Session Retention**: Mounts your existing supporter tokens (`~/.spotiflac-next`) so downloads function immediately without re-authenticating.

---

## Prerequisites

**SpotiFLAC-Next** is maintained by [spotbye](https://github.com/spotbye/SpotiFLAC-Next) as a supporter build for project donors (via [coffee.spotbye.qzz.io](https://coffee.spotbye.qzz.io), Patreon, or other supported donation methods).

Because this binary is distributed to supporters, it is **not** bundled into this repository. You must supply your own Linux AppImage file to run the container.

---

## Directory Overview

```
spotiflac-headless/
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
git clone https://github.com/your-username/spotiflac-headless.git
cd spotiflac-headless
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
