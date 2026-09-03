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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# Configure proper MIME types
mimetypes.init()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/x-icon", ".ico")

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

EVENT_HOOK_JS = """
(function() {
    if (!window.__wails_event_hooked) {
        window.__wails_event_hooked = true;
        window.__wails_event_buf = [];
        if (window.wails && window.wails.EventsNotify) {
            var _origNotify = window.wails.EventsNotify;
            window.wails.EventsNotify = function(raw) {
                try {
                    var p = typeof raw === 'string' ? JSON.parse(raw) : raw;
                    if (p && p.name) {
                        window.__wails_event_buf.push({ name: p.name, data: p.data });
                        if (window.__wails_event_buf.length > 500) {
                            window.__wails_event_buf.shift();
                        }
                    }
                } catch(e) {}
                return _origNotify.apply(this, arguments);
            };
        }
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

        # Check if actual static file exists in WEB_DIR
        local_file_path = os.path.normpath(os.path.join(WEB_DIR, clean_path.lstrip("/")))
        if os.path.isfile(local_file_path):
            return super().do_GET()

        # SPA Routing: Any non-API route serves index.html so client-side routing works
        if not clean_path.startswith("/api/") and clean_path not in ("/eval",):
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
    # Start background event bridging loop
    threading.Thread(target=event_bridge_loop, daemon=True).start()

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
