"""Local signed-webhook receiver. Replace the transaction with your pipeline's queue."""

import hashlib
import hmac
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def main():
    secret = os.environ["STORYHOOK_WEBHOOK_SECRET"].encode()
    path = Path("data/receiver.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, body BLOB NOT NULL)")

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 10 * 1024 * 1024:
                    raise ValueError("body size")
                body = self.rfile.read(length)
                stamp = self.headers["X-Storyhook-Timestamp"]
                if abs(time.time() - int(stamp)) > 300:
                    raise ValueError("timestamp")
                digest = hmac.new(secret, stamp.encode() + b"." + body, hashlib.sha256)
                expected = "sha256=" + digest.hexdigest()
                if not hmac.compare_digest(self.headers.get("X-Storyhook-Signature", ""), expected):
                    raise ValueError("signature")
                event = json.loads(body)
                if event["id"] != self.headers["Idempotency-Key"]:
                    raise ValueError("id")
            except (ValueError, KeyError, TypeError):
                self.send_response(401)
                self.end_headers()
                return
            # Acknowledge only after durable enqueue. Duplicate retries are harmless.
            with db:
                db.execute("INSERT OR IGNORE INTO inbox VALUES (?,?)", (event["id"], body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    print("Listening at http://127.0.0.1:8080/stories", flush=True)
    HTTPServer(("127.0.0.1", 8080), Receiver).serve_forever()


if __name__ == "__main__":
    main()
