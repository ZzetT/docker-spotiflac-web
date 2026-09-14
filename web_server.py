#!/usr/bin/env python3
"""
SpotiFLAC-Next Web Server & RPC Bridge
Serves the official extracted React/Tailwind/Radix UI and provides the RPC bridge
(HTTP and Server-Sent Events) connecting the web browser to the headless Wails Go backend.
"""

import sys
import os
import json
import re
import queue
import mimetypes
import threading
import time
import http.client
import urllib.parse
import zipfile
import tempfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# Configure proper MIME types
mimetypes.init()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/x-icon", ".ico")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("text/plain", ".lrc")

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8081")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LISTEN_PORT = int(os.environ.get("PORT", "8080"))

# SSE client subscriber queues
sse_clients = []
sse_clients_lock = threading.Lock()

# Event history ring buffer
event_history = []
event_history_lock = threading.Lock()
MAX_EVENT_HISTORY = 300

def format_file_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.1f} {units[i]}"

def resolve_target_path(target: str) -> str:
    target = (target or "").strip()
    if not target:
        target = "/root/Music"
    if os.path.exists(target):
        return os.path.abspath(target)
    # Map container /root/Music to local downloads directory if outside Docker
    local_downloads = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    if target.startswith("/root/Music"):
        rel = os.path.relpath(target, "/root/Music")
        cand = os.path.normpath(os.path.join(local_downloads, rel))
        if os.path.exists(cand):
            return cand
    elif target.startswith("./downloads") or target.startswith("downloads"):
        rel = target.lstrip("./").lstrip("downloads").lstrip("/")
        cand = os.path.normpath(os.path.join(local_downloads, rel))
        if os.path.exists(cand):
            return cand
    if os.path.exists(local_downloads):
        return local_downloads
    return target

EVENT_HOOK_JS = """
(function() {
    if (!window.__wails_event_hooked) {
        window.__wails_event_hooked = true;
        window.__wails_event_buf = [];
        window._wails = window._wails || {};
        var _origDispatch = window._wails.dispatchWailsEvent;
        window._wails.dispatchWailsEvent = function(ev) {
            try {
                if (ev && ev.name) {
                    window.__wails_event_buf.push({ name: ev.name, data: ev.data });
                    if (window.__wails_event_buf.length > 500) {
                        window.__wails_event_buf.shift();
                    }
                }
            } catch(e) {}
            if (_origDispatch) return _origDispatch.apply(this, arguments);
        };
    }
    var evs = window.__wails_event_buf || [];
    window.__wails_event_buf = [];
    return evs;
})()
"""

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

def event_bridge_loop():
    """
    Background worker that continuously drains events emitted by SpotiFLAC-Next's
    Go runtime (via window.wails.EventsNotify) and broadcasts them to SSE browser clients.
    """
    seq = 0
    while True:
        time.sleep(0.25)
        try:
            res = bridge_eval(EVENT_HOOK_JS, timeout=5)
            if res.get("success"):
                ev_list = res.get("result")
                if isinstance(ev_list, list) and ev_list:
                    for ev in ev_list:
                        seq += 1
                        record = {
                            "id": seq,
                            "name": ev.get("name"),
                            "data": ev.get("data"),
                            "ts": time.time()
                        }
                        with event_history_lock:
                            event_history.append(record)
                            if len(event_history) > MAX_EVENT_HISTORY:
                                event_history.pop(0)

                        with sse_clients_lock:
                            for q in list(sse_clients):
                                try:
                                    q.put_nowait(record)
                                except queue.Full:
                                    pass
        except Exception:
            pass

class SpotiFLACRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        clean_path = parsed_url.path

        # Health endpoint
        if clean_path == "/health":
            self.send_json({"status": "ok", "timestamp": time.time()})
            return

        # Status endpoint (inspects desktop title and supporter session)
        if clean_path == "/api/status":
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

        # Real-time Server-Sent Events (SSE) stream for Wails events
        if clean_path == "/api/wails/events/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            client_q = queue.Queue(maxsize=200)
            with sse_clients_lock:
                sse_clients.append(client_q)

            try:
                # Send initial handshake event
                init_ev = json.dumps({"name": "wails:connected", "data": []})
                self.wfile.write(f"data: {init_ev}\n\n".encode("utf-8"))
                self.wfile.flush()

                while True:
                    try:
                        record = client_q.get(timeout=15)
                        data_str = json.dumps(record, ensure_ascii=False)
                        self.wfile.write(f"data: {data_str}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        # Heartbeat comment to keep HTTP connection alive
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, Exception):
                pass
            finally:
                with sse_clients_lock:
                    if client_q in sse_clients:
                        sse_clients.remove(client_q)
            return

        # Polling fallback for events
        if clean_path == "/api/wails/events":
            query_params = urllib.parse.parse_qs(parsed_url.query)
            since_id = int(query_params.get("since", [0])[0])
            with event_history_lock:
                new_events = [ev for ev in event_history if ev["id"] > since_id]
            self.send_json({"success": True, "events": new_events})
            return

        # Target Container Directory Browser API (for headless web UI folder picker)
        if clean_path == "/api/target/folders":
            query = urllib.parse.parse_qs(parsed_url.query)
            target = query.get("path", ["/root/Music"])[0].strip() or "/root/Music"
            resolved = resolve_target_path(target)
            if not os.path.isdir(resolved):
                default_resolved = resolve_target_path("/root/Music")
                if os.path.isdir(default_resolved):
                    resolved = default_resolved
                else:
                    resolved = "/"
            try:
                folders = []
                parent = os.path.dirname(os.path.normpath(resolved))
                with os.scandir(resolved) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=True):
                                folders.append({"name": entry.name, "path": entry.path})
                        except Exception:
                            pass
                folders.sort(key=lambda x: x["name"].lower())
                self.send_json({
                    "current": os.path.normpath(resolved),
                    "parent": parent if parent != os.path.normpath(resolved) else None,
                    "folders": folders
                })
            except Exception as e:
                self.send_json({
                    "current": target,
                    "parent": None,
                    "folders": [],
                    "error": str(e)
                }, status=500)
            return

        # Target Container File & Directory Inspector API (for in-app artifact folder inspector)
        if clean_path == "/api/target/files":
            query = urllib.parse.parse_qs(parsed_url.query)
            target = query.get("path", ["/root/Music"])[0].strip() or "/root/Music"
            resolved = resolve_target_path(target)
            focused_file = None
            if os.path.isfile(resolved):
                focused_file = os.path.basename(resolved)
                resolved = os.path.dirname(resolved)
            elif not os.path.exists(resolved):
                parent_dir = os.path.dirname(resolved)
                if os.path.isdir(parent_dir):
                    resolved = parent_dir
                else:
                    resolved = resolve_target_path("/root/Music")

            try:
                folders = []
                files = []
                total_size = 0
                audio_count = 0
                lyrics_count = 0
                image_count = 0
                parent = os.path.dirname(os.path.normpath(resolved))

                audio_exts = {".flac", ".mp3", ".m4a", ".wav", ".ogg", ".aac", ".alac", ".aiff", ".opus"}
                lyrics_exts = {".lrc", ".txt"}
                image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

                with os.scandir(resolved) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=True):
                                child_count = 0
                                child_size = 0
                                try:
                                    with os.scandir(entry.path) as sub_it:
                                        for s_entry in sub_it:
                                            child_count += 1
                                            if s_entry.is_file(follow_symlinks=True):
                                                child_size += s_entry.stat().st_size
                                except Exception:
                                    pass
                                folders.append({
                                    "name": entry.name,
                                    "path": entry.path,
                                    "count": child_count,
                                    "size": child_size,
                                    "formatted_size": format_file_size(child_size)
                                })
                            elif entry.is_file(follow_symlinks=True):
                                stat = entry.stat()
                                sz = stat.st_size
                                total_size += sz
                                ext = os.path.splitext(entry.name)[1].lower()
                                if ext in audio_exts:
                                    ftype = "audio"
                                    audio_count += 1
                                elif ext in lyrics_exts:
                                    ftype = "lyrics"
                                    lyrics_count += 1
                                elif ext in image_exts:
                                    ftype = "image"
                                    image_count += 1
                                else:
                                    ftype = "other"

                                files.append({
                                    "name": entry.name,
                                    "path": entry.path,
                                    "size": sz,
                                    "formatted_size": format_file_size(sz),
                                    "ext": ext.lstrip(".").upper(),
                                    "type": ftype,
                                    "mtime": int(stat.st_mtime),
                                    "url": f"/api/target/file/raw?path={urllib.parse.quote(entry.path)}",
                                    "download_url": f"/api/target/file/raw?download=1&path={urllib.parse.quote(entry.path)}"
                                })
                        except Exception:
                            pass

                folders.sort(key=lambda x: x["name"].lower())
                type_priority = {"audio": 0, "lyrics": 1, "image": 2, "other": 3}
                files.sort(key=lambda x: (type_priority.get(x["type"], 9), x["name"].lower()))

                folder_name = os.path.basename(os.path.normpath(resolved)) or "Music"

                self.send_json({
                    "success": True,
                    "current": os.path.normpath(resolved),
                    "original_path": target,
                    "folder_name": folder_name,
                    "parent": parent if parent != os.path.normpath(resolved) else None,
                    "total_files": len(files),
                    "total_size": total_size,
                    "formatted_total_size": format_file_size(total_size),
                    "audio_count": audio_count,
                    "lyrics_count": lyrics_count,
                    "image_count": image_count,
                    "focused_file": focused_file,
                    "folders": folders,
                    "files": files,
                    "zip_url": f"/api/target/zip?path={urllib.parse.quote(resolved)}"
                })
            except Exception as e:
                self.send_json({
                    "success": False,
                    "current": target,
                    "error": str(e),
                    "folders": [],
                    "files": []
                }, status=500)
            return

        # Target Container File Streaming & Direct Download API (supports HTTP Range for audio seeking)
        if clean_path == "/api/target/file/raw":
            query = urllib.parse.parse_qs(parsed_url.query)
            target = query.get("path", [""])[0].strip()
            is_download = query.get("download", ["0"])[0] == "1"
            if not target:
                self.send_json({"error": "Path parameter is required"}, status=400)
                return

            resolved = resolve_target_path(target)
            if not os.path.isfile(resolved):
                self.send_json({"error": f"File not found: {target}"}, status=404)
                return

            try:
                file_size = os.path.getsize(resolved)
                mime_type, _ = mimetypes.guess_type(resolved)
                if not mime_type:
                    ext = os.path.splitext(resolved)[1].lower()
                    if ext == ".flac":
                        mime_type = "audio/flac"
                    elif ext == ".lrc":
                        mime_type = "text/plain; charset=utf-8"
                    elif ext in (".m4a", ".alac"):
                        mime_type = "audio/mp4"
                    else:
                        mime_type = "application/octet-stream"

                range_header = self.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    range_val = range_header[6:].strip()
                    parts = range_val.split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

                    if start >= file_size or end >= file_size or start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.end_headers()
                        return

                    length = end - start + 1
                    self.send_response(206)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    if is_download:
                        filename = os.path.basename(resolved)
                        self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(filename)}"')
                    self.end_headers()

                    with open(resolved, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(65536, remaining)
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return

                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                if is_download:
                    filename = os.path.basename(resolved)
                    self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(filename)}"')
                self.end_headers()

                with open(resolved, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
                return

        # Target Container Folder ZIP Archive Download API
        if clean_path == "/api/target/zip":
            query = urllib.parse.parse_qs(parsed_url.query)
            target = query.get("path", [""])[0].strip()
            if not target:
                self.send_json({"error": "Path parameter is required"}, status=400)
                return

            resolved = resolve_target_path(target)
            if os.path.isfile(resolved):
                resolved = os.path.dirname(resolved)

            if not os.path.isdir(resolved):
                self.send_json({"error": f"Directory not found: {target}"}, status=404)
                return

            try:
                folder_name = os.path.basename(os.path.normpath(resolved)) or "Music"
                zip_filename = f"{folder_name}.zip"

                with tempfile.NamedTemporaryFile(suffix=".zip") as tmp_zip:
                    with zipfile.ZipFile(tmp_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                        for root, dirs, files in os.walk(resolved):
                            for f in files:
                                full_fpath = os.path.join(root, f)
                                rel_fpath = os.path.relpath(full_fpath, resolved)
                                zf.write(full_fpath, arcname=rel_fpath)
                    tmp_zip.seek(0)
                    zip_size = os.path.getsize(tmp_zip.name)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(zip_filename)}"')
                    self.send_header("Content-Length", str(zip_size))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    while True:
                        chunk = tmp_zip.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
                return

        # Check if actual static file exists in WEB_DIR
        local_file_path = os.path.normpath(os.path.join(WEB_DIR, clean_path.lstrip("/")))
        if os.path.isfile(local_file_path):
            return super().do_GET()

        # Wails v3 GET endpoints: /wails/*
        if clean_path.startswith("/wails/"):
            js = f"""
            return (async () => {{
                try {{
                    const res = await fetch('{clean_path}', {{ method: 'GET' }});
                    const txt = await res.text();
                    const hdrs = {{}};
                    for (const [k, v] of res.headers.entries()) {{
                        hdrs[k] = v;
                    }}
                    return {{ ok: res.ok, status: res.status, headers: hdrs, body: txt }};
                }} catch (e) {{
                    return {{ ok: false, status: 500, headers: {{}}, body: e.message || String(e) }};
                }}
            }})();
            """
            res = bridge_eval(js)
            if res.get("success") and isinstance(res.get("result"), dict):
                r_dict = res["result"]
                status_code = r_dict.get("status", 200)
                body_str = r_dict.get("body", "")
                self.send_response(status_code)
                resp_headers = r_dict.get("headers", {})
                ct = resp_headers.get("content-type", "application/javascript; charset=utf-8" if clean_path.endswith(".js") else "text/plain; charset=utf-8")
                self.send_header("Content-Type", ct)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body_str.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(body_str.encode("utf-8"))
            else:
                self.send_json({"success": False, "error": res.get("error", "Not found")}, status=404)
            return

        # SPA Routing: Any non-API route serves index.html so client-side routing works
        if not clean_path.startswith("/api/") and clean_path not in ("/eval",) and not clean_path.startswith("/wails/"):
            index_path = os.path.join(WEB_DIR, "index.html")
            assets_path = os.path.join(WEB_DIR, "assets")
            if os.path.exists(index_path) and os.path.isdir(assets_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(index_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                # Check whether AppImage exists on system
                appimage_found = False
                for d in ("/app/appimage", os.path.join(os.path.dirname(os.path.abspath(__file__)), "appimage"), "/app"):
                    if os.path.isdir(d):
                        for fname in os.listdir(d):
                            if fname.endswith(".AppImage"):
                                appimage_found = True
                                break
                    if appimage_found:
                        break
                if not appimage_found and os.path.isfile("/app/squashfs-root/AppRun"):
                    appimage_found = True

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()

                if not appimage_found:
                    self.wfile.write(b"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>SpotiFLAC-Next AppImage Required</title>
    <style>
        * { box-sizing: border-box; }
        body { background: #09090b; color: #e4e4e7; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }
        .card { background: #18181b; border: 1px solid #27272a; border-radius: 12px; max-width: 540px; width: 100%; padding: 32px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .icon { width: 48px; height: 48px; background: rgba(239, 68, 68, 0.15); color: #ef4444; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px; margin-bottom: 20px; }
        h1 { font-size: 20px; font-weight: 600; margin: 0 0 12px; color: #fafafa; }
        p { color: #a1a1aa; font-size: 14px; line-height: 1.6; margin: 0 0 16px; }
        .code-box { background: #09090b; border: 1px solid #27272a; border-radius: 8px; padding: 12px 16px; font-family: ui-monospace, monospace; font-size: 13px; color: #10b981; word-break: break-all; margin-bottom: 20px; }
        .pulse { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #eab308; margin-right: 8px; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.8); } }
        .status { font-size: 13px; color: #eab308; display: flex; align-items: center; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">&#9888;</div>
        <h1>SpotiFLAC-Next AppImage Required</h1>
        <p>SpotiFLAC-Next is a supporter build available to donors supporting development. To run this container, place your Linux <code>.AppImage</code> file into the <code>appimage/</code> folder:</p>
        <div class="code-box">./appimage/SpotiFLAC-Next.AppImage</div>
        <p>Once placed, the container will automatically detect the file, extract the official React frontend, and load the application.</p>
        <div class="status"><span class="pulse"></span> Monitoring for AppImage file (auto-refreshes every 5s)...</div>
    </div>
</body>
</html>""")
                    return

                self.wfile.write(b"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="3">
    <title>Initializing SpotiFLAC Web UI...</title>
    <style>
        body { background: #09090b; color: #e4e4e7; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }
        .spinner { width: 40px; height: 40px; margin: 0 auto 20px; border: 3px solid rgba(255,255,255,0.1); border-top-color: #10b981; border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        h2 { margin: 0 0 10px; font-weight: 600; color: #f4f4f5; }
        p { color: #a1a1aa; margin: 0; font-size: 14px; }
    </style>
</head>
<body>
    <div>
        <div class="spinner"></div>
        <h2>Initializing SpotiFLAC Web UI</h2>
        <p>Extracting embedded web assets from AppImage. This page refreshes automatically...</p>
    </div>
</body>
</html>""")
                return

        super().do_GET()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        # Diagnostics eval endpoint (if explicitly enabled)
        if self.path in ("/eval", "/api/eval"):
            if os.environ.get("DEBUG_EVAL") == "1":
                res = bridge_eval(post_body)
                self.send_json(res)
            else:
                self.send_json({"success": False, "error": "Endpoint disabled for security. Set DEBUG_EVAL=1 to enable."}, status=403)
            return

        # Target Container Directory Creation API
        if self.path == "/api/target/folders/create":
            try:
                data = json.loads(post_body) if post_body else {}
                folder_to_create = data.get("path", "").strip()
                if folder_to_create:
                    os.makedirs(folder_to_create, exist_ok=True)
                    self.send_json({"success": True, "path": folder_to_create})
                else:
                    self.send_json({"success": False, "error": "Empty folder path"}, status=400)
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, status=500)
            return

        # Wails v3 Runtime & Stream Proxy: /wails/*
        if self.path.startswith("/wails/"):
            try:
                p_check = json.loads(post_body) if post_body else {}
                # Prevent Application.Quit from shutting down headless server
                if p_check.get("object") == 2 and p_check.get("method") == 2:
                    self.send_json({"ok": True})
                    return
                # Prevent desktop modal dialogs from blocking headless GTK loop
                mid = p_check.get("args", {}).get("methodID")
                if mid == 237181597:  # main.App.SelectFolder
                    current = p_check.get("args", {}).get("args", [""])[0] if p_check.get("args", {}).get("args") else ""
                    self.send_json(current or "/root/Music")
                    return
                if mid in (2427571203, 3818965540, 3561358672):  # main.App.SelectFile / AudioFiles / LyricsFiles
                    self.send_json([])
                    return
                if mid in (3894305329, 431469111):  # main.App.OpenFolder / OpenConfigFolder
                    self.send_json(None)
                    return
            except Exception:
                pass

            forward_headers = {}
            for h in ("x-wails-client-id", "x-wails-window-name", "x-wails-chunk-id", "x-wails-chunk-index", "x-wails-chunk-total", "content-type"):
                val = self.headers.get(h)
                if val:
                    forward_headers[h] = val
            headers_js = json.dumps(forward_headers)
            body_js = json.dumps(post_body)
            js = f"""
            return (async () => {{
                try {{
                    const res = await fetch('{self.path}', {{
                        method: 'POST',
                        headers: {headers_js},
                        body: {body_js}
                    }});
                    const txt = await res.text();
                    const hdrs = {{}};
                    for (const [k, v] of res.headers.entries()) {{
                        hdrs[k] = v;
                    }}
                    return {{ ok: res.ok, status: res.status, headers: hdrs, body: txt }};
                }} catch (e) {{
                    return {{ ok: false, status: 500, headers: {{}}, body: e.message || String(e) }};
                }}
            }})();
            """
            res = bridge_eval(js)
            if res.get("success") and isinstance(res.get("result"), dict):
                r_dict = res["result"]
                status_code = r_dict.get("status", 200)
                body_str = r_dict.get("body", "")
                self.send_response(status_code)
                resp_headers = r_dict.get("headers", {})
                ct = resp_headers.get("content-type", "application/json; charset=utf-8")
                self.send_header("Content-Type", ct)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body_str.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(body_str.encode("utf-8"))
            else:
                err_msg = res.get("error", "Failed to forward Wails v3 request")
                self.send_json({"success": False, "error": err_msg}, status=502)
            return

        try:
            payload = json.loads(post_body) if post_body else {}
        except Exception:
            self.send_json({"success": False, "error": "Invalid JSON body"}, status=400)
            return

        # Core Wails RPC Bridge: window.go.<method>(...args)
        if self.path == "/api/wails/call":
            method = payload.get("method", "").strip()
            args = payload.get("args", [])
            if not method:
                self.send_json({"success": False, "error": "Method parameter is required"}, status=400)
                return

            if not re.match(r"^[a-zA-Z0-9_\.]+$", method):
                self.send_json({"success": False, "error": "Invalid method name"}, status=400)
                return

            args_json = json.dumps(args, ensure_ascii=False)
            js = f"return (window.go.{method}(...{args_json}));"
            res = bridge_eval(js)
            self.send_json(res)
            return

        # Core Wails Event Bridge: window.runtime.EventsEmit(name, ...data)
        if self.path == "/api/wails/emit":
            name = payload.get("name", "").strip()
            data = payload.get("data", [])
            if not name:
                self.send_json({"success": False, "error": "Event name is required"}, status=400)
                return

            name_json = json.dumps(name, ensure_ascii=False)
            data_json = json.dumps(data, ensure_ascii=False)
            js = f"window.runtime.EventsEmit({name_json}, ...{data_json}); return true;"
            res = bridge_eval(js)
            self.send_json(res)
            return

        self.send_json({"success": False, "error": "Not Found"}, status=404)

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-wails-client-id, x-wails-window-name, x-wails-chunk-id, x-wails-chunk-index, x-wails-chunk-total")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        if self.path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        super().do_HEAD()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-wails-client-id, x-wails-window-name, x-wails-chunk-id, x-wails-chunk-index, x-wails-chunk-total")
        self.end_headers()

def run_server():
    # Start background event bridging loop
    threading.Thread(target=event_bridge_loop, daemon=True).start()

    host_port = os.environ.get("HOST_PORT", str(LISTEN_PORT))
    server_address = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(server_address, SpotiFLACRequestHandler)
    print(f"\n===========================================================")
    print(f"  SpotiFLAC Web UI running on port {host_port}")
    print(f"  Access in browser: http://<your-server-ip>:{host_port}")
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
