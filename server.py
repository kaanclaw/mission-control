#!/usr/bin/env python3
"""Mission Control Real-Time Status Server (threaded)"""
import json, os, time, queue, sys
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from threading import Lock
import urllib.parse

DATA_DIR = Path(__file__).parent / "data"
STATUS_FILE = DATA_DIR / "agent-status.json"
ACTIVITY_FILE = DATA_DIR / "activity-feed.json"
PORT = int(os.environ.get("MC_PORT", "8090"))
lock = Lock()
subscribers = []

def read_json(path):
    try: return json.loads(path.read_text())
    except: return {}

def write_json(path, data):
    with lock: path.write_text(json.dumps(data, indent=2))

def broadcast(msg):
    dead = []
    for q in subscribers:
        try: q.put_nowait(json.dumps(msg))
        except: dead.append(q)
    for q in dead:
        try: subscribers.remove(q)
        except: pass

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
    def do_OPTIONS(self):
        self.send_response(200); self.send_cors(); self.end_headers()
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._json_response({"status":"ok"})
        elif path == "/status":
            self._json_response(read_json(STATUS_FILE))
        elif path == "/activity":
            self._json_response(read_json(ACTIVITY_FILE) or {"events":[]})
        elif path == "/stream":
            self._sse_stream()
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if path == "/update":
            data = read_json(STATUS_FILE) or {"agents":[]}
            for a in data.get("agents",[]):
                if a["id"] == body.get("agent_id"):
                    a["status"] = body.get("status", a["status"])
                    a["task"] = body.get("task", a["task"])
                    a["status_since"] = body.get("status_since", datetime.now(timezone.utc).isoformat())
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            write_json(STATUS_FILE, data)
            broadcast({"type":"status","data":data})
            self._json_response({"ok":True})
        elif path == "/log":
            aid = body.get("agent_id","claw")
            line = body.get("line","")
            log_file = DATA_DIR / "agent-logs" / f"{aid}.txt"
            log_file.parent.mkdir(exist_ok=True)
            with lock:
                with open(log_file,"a") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {line}\n")
            broadcast({"type":"log","agent_id":aid,"line":line})
            self._json_response({"ok":True})
        elif path == "/activity/add":
            feed = read_json(ACTIVITY_FILE) or {"events":[]}
            body["timestamp"] = datetime.now(timezone.utc).isoformat()
            feed["events"].insert(0, body)
            feed["events"] = feed["events"][:100]
            write_json(ACTIVITY_FILE, feed)
            broadcast({"type":"activity","event":body})
            self._json_response({"ok":True})
        else:
            self.send_response(404); self.end_headers()
    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200); self.send_cors()
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def _sse_stream(self):
        q = queue.Queue(maxsize=50)
        subscribers.append(q)
        self.send_response(200); self.send_cors()
        self.send_header("Content-Type","text/event-stream")
        self.send_header("Cache-Control","no-cache")
        self.send_header("X-Accel-Buffering","no")
        self.end_headers()
        try:
            snap = json.dumps({"type":"snapshot","status":read_json(STATUS_FILE),"activity":read_json(ACTIVITY_FILE) or {"events":[]}})
            self.wfile.write(f"data: {snap}\n\n".encode()); self.wfile.flush()
        except: return
        while True:
            try:
                msg = q.get(timeout=25)
                self.wfile.write(f"data: {msg}\n\n".encode()); self.wfile.flush()
            except queue.Empty:
                try: self.wfile.write(b": heartbeat\n\n"); self.wfile.flush()
                except: break
            except: break
        try: subscribers.remove(q)
        except: pass

class ThreadedServer(ThreadingMixIn, HTTPServer): pass

if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "agent-logs").mkdir(exist_ok=True)
    if not ACTIVITY_FILE.exists(): ACTIVITY_FILE.write_text('{"events":[]}')
    print(f"Mission Control status server on http://localhost:{PORT}")
    ThreadedServer(("0.0.0.0", PORT), Handler).serve_forever()
