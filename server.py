#!/usr/bin/env python3
"""
EasyClaw Mission Control — Real-Time Status Server
Streams agent status, task progress, and activity feed via SSE.
"""
import json
import os
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Lock
import urllib.parse

DATA_DIR = Path(__file__).parent / "data"
STATUS_FILE = DATA_DIR / "agent-status.json"
ACTIVITY_FILE = DATA_DIR / "activity-feed.json"
PORT = int(os.environ.get("MC_PORT", "8090"))

lock = Lock()
subscribers = []  # list of queues for SSE clients

def read_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except:
        return {"agents": [], "last_updated": datetime.now(timezone.utc).isoformat()}

def read_activity():
    try:
        return json.loads(ACTIVITY_FILE.read_text())
    except:
        return {"events": []}

def write_status(data):
    with lock:
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        STATUS_FILE.write_text(json.dumps(data, indent=2))
    broadcast({"type": "status", "data": data})

def add_activity(event):
    with lock:
        feed = read_activity()
        feed["events"].insert(0, {
            **event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": int(time.time() * 1000)
        })
        feed["events"] = feed["events"][:100]  # keep last 100
        ACTIVITY_FILE.write_text(json.dumps(feed, indent=2))
    broadcast({"type": "activity", "event": event})

def broadcast(msg):
    dead = []
    for q in subscribers:
        try:
            q.put_nowait(json.dumps(msg))
        except:
            dead.append(q)
    for q in dead:
        try: subscribers.remove(q)
        except: pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence access logs

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/health":
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        elif path == "/status":
            data = read_status()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/activity":
            data = read_activity()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/stream":
            # SSE endpoint
            import queue
            q = queue.Queue(maxsize=50)
            subscribers.append(q)

            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            # Send initial snapshot
            try:
                snap = json.dumps({"type": "snapshot", "status": read_status(), "activity": read_activity()})
                self.wfile.write(f"data: {snap}\n\n".encode())
                self.wfile.flush()
            except:
                return

            # Stream updates
            while True:
                try:
                    msg = q.get(timeout=25)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                    self.wfile.flush()
                except:
                    # Heartbeat
                    try:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    except:
                        break

            try: subscribers.remove(q)
            except: pass

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == "/update":
            # Update a single agent's status
            # Body: {agent_id, status, task, status_since?}
            data = read_status()
            for agent in data.get("agents", []):
                if agent["id"] == body.get("agent_id"):
                    agent["status"] = body.get("status", agent["status"])
                    agent["task"] = body.get("task", agent["task"])
                    agent["status_since"] = body.get("status_since", datetime.now(timezone.utc).isoformat())
            write_status(data)

            # Also log to activity feed
            add_activity({
                "type": "agent_update",
                "agent_id": body.get("agent_id"),
                "agent_name": body.get("agent_name", body.get("agent_id", "").title()),
                "status": body.get("status"),
                "task": body.get("task"),
                "icon": "🤖"
            })

            resp = b'{"ok":true}'
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        elif path == "/log":
            # Append a log line for an agent
            # Body: {agent_id, line}
            agent_id = body.get("agent_id", "claw")
            line = body.get("line", "")
            log_file = DATA_DIR / "agent-logs" / f"{agent_id}.txt"
            log_file.parent.mkdir(exist_ok=True)
            with lock:
                with open(log_file, "a") as f:
                    ts = datetime.now().strftime("%H:%M:%S")
                    f.write(f"[{ts}] {line}\n")
            broadcast({"type": "log", "agent_id": agent_id, "line": line})
            resp = b'{"ok":true}'
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        elif path == "/activity/add":
            add_activity(body)
            resp = b'{"ok":true}'
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    # Ensure data files exist
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "agent-logs").mkdir(exist_ok=True)
    if not ACTIVITY_FILE.exists():
        ACTIVITY_FILE.write_text('{"events":[]}')

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Mission Control status server running on http://localhost:{PORT}")
    print(f"  SSE stream:    GET  /stream")
    print(f"  Update agent:  POST /update  {{agent_id, status, task}}")
    print(f"  Add log line:  POST /log     {{agent_id, line}}")
    print(f"  Add activity:  POST /activity/add {{type, ...}}")
    server.serve_forever()
