#!/usr/bin/env python3
"""
SpotiFLAC-Next Web Server
Provides an HTTP web interface and REST API for controlling SpotiFLAC-Next headlessly.
"""

import sys
import os
import json
import re
import urllib.request
import urllib.error
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8081")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LISTEN_PORT = int(os.environ.get("PORT", "8080"))

# In-memory download task tracker for the web UI
download_tasks = []
download_tasks_lock = threading.Lock()

import http.client
import urllib.parse

def bridge_eval(js_code: str, timeout: int = 120) -> dict:
    try:
        parsed = urllib.parse.urlparse(BRIDGE_URL)
        conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 8081, timeout=timeout)
        payload = js_code.encode("utf-8")
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Length": str(len(payload))
        }
        conn.request("POST", "/eval", body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        conn.close()
        return json.loads(data)
    except Exception as e:
        return {"success": False, "error": str(e)}

def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://") or text.startswith("spotify:")

def detect_url_type(url: str) -> str:
    url_lower = url.lower()
    if "amazon." in url_lower and ("/albums/" in url_lower or "/tracks/" in url_lower or "/playlists/" in url_lower):
        return "amazon"
    if "spotify.com" in url_lower or url_lower.startswith("spotify:"):
        return "spotify"
    if "tidal.com" in url_lower:
        return "tidal"
    if "qobuz.com" in url_lower:
        return "qobuz"
    if "deezer.com" in url_lower:
        return "deezer"
    return "unknown"

def normalize_spotify_url(target: str) -> str:
    if target.startswith("http://") or target.startswith("https://"):
        return target
    if target.startswith("spotify:album:"):
        return f"https://open.spotify.com/album/{target.split(':')[-1]}"
    if target.startswith("spotify:track:"):
        return f"https://open.spotify.com/track/{target.split(':')[-1]}"
    if len(target) == 22 and target.isalnum():
        return f"https://open.spotify.com/album/{target}"
    return target

def fetch_url_metadata(url: str) -> dict:
    url_type = detect_url_type(url)
    
    if url_type == "spotify":
        clean_url = normalize_spotify_url(url)
        js = f"window.go.main.App.GetSpotifyMetadata({{ url: {json.dumps(clean_url)}, batch: true, delay: 1, timeout: 300, separator: ', ' }})"
        res = bridge_eval(js)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", "Failed to fetch Spotify metadata")}
        
        raw = res.get("result")
        meta = json.loads(raw) if isinstance(raw, str) else raw
        
        # Normalize into unified format
        album_info = meta.get("album_info", {})
        track_list = meta.get("track_list", [])
        
        normalized_tracks = []
        for t in track_list:
            normalized_tracks.append({
                "id": t.get("spotify_id") or t.get("id"),
                "name": t.get("name"),
                "artists": t.get("artists", album_info.get("artists")),
                "album_name": album_info.get("name"),
                "album_artist": album_info.get("primary_artist") or album_info.get("artists"),
                "track_number": t.get("track_number", 1),
                "disc_number": t.get("disc_number", 1),
                "total_tracks": len(track_list),
                "total_discs": t.get("total_discs", 1),
                "duration_ms": t.get("duration_ms", 0),
                "isrc": t.get("isrc", ""),
                "images": t.get("images") or album_info.get("images"),
                "release_date": album_info.get("release_date", ""),
                "url": t.get("external_urls") or clean_url,
                "source": "spotify"
            })
            
        return {
            "success": True,
            "source": "spotify",
            "album_info": {
                "name": album_info.get("name", "Unknown Album"),
                "artists": album_info.get("artists", "Unknown Artist"),
                "images": album_info.get("images", ""),
                "release_date": album_info.get("release_date", ""),
                "total_tracks": len(track_list),
                "url": clean_url
            },
            "track_list": normalized_tracks
        }
        
    elif url_type in ("amazon", "tidal", "qobuz", "deezer"):
        js = f"window.go.main.App.GetDirectLinkMetadata({json.dumps(url)})"
        res = bridge_eval(js)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", f"Failed to fetch {url_type} metadata")}
        
        raw = res.get("result")
        meta = json.loads(raw) if isinstance(raw, str) else raw
        
        album_info = meta.get("album_info", {})
        track_list = meta.get("track_list", [])
        
        normalized_tracks = []
        for t in track_list:
            normalized_tracks.append({
                "id": t.get("spotify_id") or t.get("id"),
                "name": t.get("name"),
                "artists": t.get("artists", album_info.get("artists")),
                "album_name": album_info.get("name"),
                "album_artist": album_info.get("primary_artist") or album_info.get("artists"),
                "track_number": t.get("track_number", 1),
                "disc_number": t.get("disc_number", 1),
                "total_tracks": len(track_list),
                "total_discs": t.get("total_discs", 1),
                "duration_ms": t.get("duration_ms", 0),
                "isrc": t.get("isrc", ""),
                "images": t.get("images") or album_info.get("images"),
                "release_date": album_info.get("release_date", ""),
                "url": t.get("external_urls") or t.get("album_url") or url,
                "source": url_type
            })
            
        return {
            "success": True,
            "source": url_type,
            "album_info": {
                "name": album_info.get("name", "Unknown Album"),
                "artists": album_info.get("artists", "Unknown Artist"),
                "images": album_info.get("images", ""),
                "release_date": album_info.get("release_date", ""),
                "total_tracks": len(track_list),
                "url": url
            },
            "track_list": normalized_tracks
        }
    else:
        return {"success": False, "error": f"Unsupported or unrecognized URL: {url}"}

def download_track_worker(track_data: dict, album_info: dict, task_id: str):
    # Fetch settings to get download path
    settings_res = bridge_eval("window.go.main.App.LoadSettings()")
    settings = {}
    if settings_res.get("success"):
        s = settings_res.get("result")
        settings = json.loads(s) if isinstance(s, str) else (s or {})
    
    download_dir = settings.get("downloadPath", "/root/Downloads")
    if not os.path.exists(download_dir):
        download_dir = "/root/Downloads"

    track_id = track_data.get("id", "")
    service = ""
    service_url = track_data.get("url", "")
    if track_id.startswith("amazon_"):
        service = "amazon"
    elif track_id.startswith("tidal_"):
        service = "tidal"
    elif track_id.startswith("qobuz_"):
        service = "qobuz"
    elif track_id.startswith("deezer_"):
        service = "deezer"

    pos = int(track_data.get("track_number", 1))

    album_name = album_info.get("name", track_data.get("album_name", "")).replace("/", "-")
    album_artist = album_info.get("artists", track_data.get("album_artist", "")).replace("/", "-")
    artist_name = track_data.get("artists", "").replace("/", "-")
    release_date = album_info.get("release_date", "")
    year = release_date.split("-")[0] if release_date else ""

    # Resolve subfolder structure from settings (folderTemplate)
    folder_template = settings.get("folderTemplate", "")
    output_dir = download_dir
    if folder_template:
        sub = folder_template.replace("{album_artist}", album_artist) \
                             .replace("{artist}", artist_name) \
                             .replace("{album}", album_name) \
                             .replace("{year}", year) \
                             .replace("{year_prefix}", f"({year}) " if year else "")
        sub_parts = [p.strip() for p in sub.split("/") if p.strip()]
        if sub_parts:
            output_dir = os.path.join(download_dir, *sub_parts)

    req = {
        "spotify_id": track_id,
        "track_name": track_data.get("name", ""),
        "artist_name": track_data.get("artists", ""),
        "artists": track_data.get("artists", ""),
        "primary_artist": track_data.get("artists", ""),
        "album_name": album_info.get("name", track_data.get("album_name", "")),
        "album_artist": album_info.get("artists", track_data.get("album_artist", "")),
        "album_artists": album_info.get("artists", ""),
        "primary_album_artist": album_info.get("artists", ""),
        "is_explicit": track_data.get("is_explicit", False),
        "category": "album",
        "playlist_name": "",
        "playlist_owner": "",
        "upc": album_info.get("upc", ""),
        "release_date": album_info.get("release_date", ""),
        "isrc": track_data.get("isrc", ""),
        "track_number": bool(settings.get("trackNumber", False)),
        "position": pos,
        "spotify_track_number": pos,
        "disc_number": int(track_data.get("disc_number", 1)),
        "spotify_disc_number": int(track_data.get("disc_number", 1)),
        "total_tracks": int(album_info.get("total_tracks", 1)),
        "spotify_total_tracks": int(album_info.get("total_tracks", 1)),
        "total_discs": int(track_data.get("total_discs", 1)),
        "spotify_total_discs": int(track_data.get("total_discs", 1)),
        "use_album_track_number": bool(settings.get("useAlbumTrackNumber", True)),
        "library_root": download_dir,
        "output_dir": output_dir,
        "filename_format": settings.get("filenameTemplate") or "{title}",
        "audio_format": "flac",
        "service": service,
        "service_url": service_url
    }
    
    with download_tasks_lock:
        for t in download_tasks:
            if t["id"] == task_id:
                t["status"] = "downloading"
                break
                
    js = f"window.go.main.App.DownloadTrack({json.dumps(req)})"
    res = bridge_eval(js, timeout=300)
    
    with download_tasks_lock:
        for t in download_tasks:
            if t["id"] == task_id:
                if res.get("success"):
                    t["status"] = "completed"
                    t["completed_at"] = time.time()
                else:
                    t["status"] = "failed"
                    t["error"] = res.get("error", "Download failed")
                break

def download_album_worker(album_info: dict, track_list: list, batch_id: str):
    for trk in track_list:
        task_id = f"{batch_id}_{trk.get('track_number', 0)}"
        with download_tasks_lock:
            download_tasks.append({
                "id": task_id,
                "batch_id": batch_id,
                "name": trk.get("name"),
                "artist": trk.get("artists"),
                "album": album_info.get("name"),
                "track_number": trk.get("track_number", 1),
                "status": "pending",
                "added_at": time.time()
            })
        download_track_worker(trk, album_info, task_id)

class SpotiFLACRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        if self.path == "/health":
            self.send_json({"status": "ok", "timestamp": time.time()})
            return

        if self.path == "/api/status":
            health = bridge_eval("document.title")
            sess = bridge_eval("window.go.main.App.LoadSupporterSession()")
            
            supporter_data = {}
            if sess.get("success"):
                s_raw = sess.get("result")
                raw_data = json.loads(s_raw) if isinstance(s_raw, str) else (s_raw or {})
                supporter_data = {
                    "present": bool(raw_data.get("present")),
                    "email": raw_data.get("email", ""),
                    "plate": raw_data.get("plate", "Supporter")
                }

            self.send_json({
                "success": health.get("success", False),
                "app_title": health.get("result", "SpotiFLAC Next"),
                "supporter": supporter_data,
                "bridge_url": BRIDGE_URL
            })
            return

        if self.path == "/api/settings":
            res = bridge_eval("window.go.main.App.LoadSettings()")
            if res.get("success"):
                raw = res.get("result", "{}")
                data = json.loads(raw) if isinstance(raw, str) else raw
                self.send_json({"success": True, "settings": data})
            else:
                self.send_json({"success": False, "error": res.get("error", "Failed to load settings")})
            return

        if self.path == "/api/queue":
            with download_tasks_lock:
                recent_tasks = list(download_tasks)[-50:]

            self.send_json({
                "success": True,
                "recent_tasks": recent_tasks
            })
            return

        super().do_GET()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        # Support POST /eval and POST /api/eval for diagnostics only when explicitly enabled
        if self.path in ("/eval", "/api/eval"):
            if os.environ.get("DEBUG_EVAL") == "1":
                res = bridge_eval(post_body)
                self.send_json(res)
            else:
                self.send_json({"success": False, "error": "Endpoint disabled for security. Set DEBUG_EVAL=1 to enable."}, status=403)
            return

        try:
            payload = json.loads(post_body) if post_body else {}
        except Exception:
            self.send_json({"success": False, "error": "Invalid JSON body"}, status=400)
            return

        if self.path == "/api/fetch":
            url = payload.get("url", "").strip()
            if not url:
                self.send_json({"success": False, "error": "URL parameter is required"}, status=400)
                return
            res = fetch_url_metadata(url)
            self.send_json(res)
            return

        if self.path == "/api/search":
            query = payload.get("query", "").strip()
            platform = payload.get("platform", "spotify").lower()
            limit = int(payload.get("limit", 10))

            if not query:
                self.send_json({"success": False, "error": "Query parameter is required"}, status=400)
                return

            if platform == "spotify":
                js = f"window.go.main.App.SearchSpotify({{ query: {json.dumps(query)}, limit: {limit} }})"
            else:
                js = f"window.go.main.App.SearchDirectLink({{ query: {json.dumps(query)}, platform: {json.dumps(platform)}, limit: {limit} }})"

            res = bridge_eval(js)
            if not res.get("success"):
                self.send_json({"success": False, "error": res.get("error", "Search failed")})
                return

            raw = res.get("result", {})
            data = json.loads(raw) if isinstance(raw, str) else raw
            self.send_json({"success": True, "result": data})
            return

        if self.path == "/api/download-track":
            track = payload.get("track")
            album = payload.get("album")
            if not track or not album:
                self.send_json({"success": False, "error": "track and album parameters are required"}, status=400)
                return

            task_id = f"task_{int(time.time()*1000)}"
            with download_tasks_lock:
                download_tasks.append({
                    "id": task_id,
                    "name": track.get("name"),
                    "artist": track.get("artists"),
                    "album": album.get("name"),
                    "track_number": track.get("track_number", 1),
                    "status": "pending",
                    "added_at": time.time()
                })

            threading.Thread(target=download_track_worker, args=(track, album, task_id), daemon=True).start()
            self.send_json({"success": True, "task_id": task_id, "message": "Download started"})
            return

        if self.path == "/api/download-album":
            album = payload.get("album")
            tracks = payload.get("tracks", [])
            if not album or not tracks:
                self.send_json({"success": False, "error": "album and tracks parameters are required"}, status=400)
                return

            batch_id = f"batch_{int(time.time()*1000)}"
            threading.Thread(target=download_album_worker, args=(album, tracks, batch_id), daemon=True).start()
            self.send_json({
                "success": True,
                "batch_id": batch_id,
                "total_tracks": len(tracks),
                "message": f"Queued {len(tracks)} tracks for download"
            })
            return

        if self.path == "/api/settings":
            new_settings = payload.get("settings", {})
            js = f"window.go.main.App.SaveSettings({json.dumps(new_settings)})"
            res = bridge_eval(js)
            self.send_json(res)
            return

        if self.path == "/api/queue/clear":
            with download_tasks_lock:
                # Retain only actively downloading or pending tasks
                download_tasks[:] = [t for t in download_tasks if t.get("status") in ("downloading", "pending")]
            self.send_json({"success": True, "message": "Cleared completed/failed tasks"})
            return

        self.send_json({"success": False, "error": "Not Found"}, status=404)

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def run_server():
    server_address = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(server_address, SpotiFLACRequestHandler)
    print(f"\n===========================================================")
    print(f"  SpotiFLAC Web UI running at http://0.0.0.0:{LISTEN_PORT}")
    print(f"  Bridge URL: {BRIDGE_URL}")
    print(f"===========================================================\n")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run_server()
