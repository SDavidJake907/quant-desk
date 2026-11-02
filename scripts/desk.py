"""The desk: one page showing what the system did, holds and decided.

    python scripts/desk.py                 # http://127.0.0.1:8765
    python scripts/desk.py --demo          # invented data, no keys needed
    python scripts/desk.py --port 9000
    python scripts/desk.py --no-news       # skip live headline fetches

Open the address in a browser. The page refreshes itself every minute.

WHAT IT SHOWS
The last scan as a pipeline from the whole universe down to orders sent, with
every refusal reason; open positions with a 30-minute chart, entry, stop and
target, indicators, the news Gemma read at entry and today's headlines;
working orders and the watchlist; Gemma's rankings, news verdicts and its
scoreboard; every past trade with P&L, R, win rate and Gemma's hindsight
review; the equity curve.

READ-ONLY
The server issues GET requests to Alpaca and reads local files. There is no
button, route or code path here that places, cancels or changes an order.

LISTENS ON 127.0.0.1 BY DEFAULT
The page shows your account. `--host 0.0.0.0` makes it reachable from other
devices on your network (a phone on the same Wi-Fi) and from anyone else on
that network too. Only do that on a network you trust.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from scanner.config import settings
from scanner.web import demo, snapshot

TEMPLATE = Path(__file__).with_name("desk_template.html")
#: Served locally so the charts work offline and do not depend on a CDN.
#: TradingView Lightweight Charts 4.2.0, Apache-2.0 (see vendor/LICENSE-*).
CHART_LIB = Path(__file__).parent / "vendor" / "lightweight-charts.js"
CACHE_SECONDS = 60


class Cache:
    """One snapshot at a time. Building one makes a few hundred requests, so
    several open tabs must share it rather than each starting their own."""

    def __init__(self, builder, ttl: float):
        self.builder, self.ttl = builder, ttl
        self.value: dict | None = None
        self.at = 0.0
        self.lock = threading.Lock()

    def get(self, refresh: bool = False) -> dict:
        with self.lock:
            if refresh or self.value is None or time.time() - self.at > self.ttl:
                self.value = self.builder()
                self.at = time.time()
            return self.value


def live_builder(news: bool):
    def build() -> dict:
        async def run() -> dict:
            async with httpx.AsyncClient(timeout=30) as client:
                return await snapshot.build(
                    client, audit_path=settings.audit_log_path,
                    key_id=settings.alpaca_key_id,
                    secret=settings.alpaca_secret_key, news=news)
        try:
            return asyncio.run(run())
        except Exception as exc:          # shown on the page, not a crash
            return {"error": f"{type(exc).__name__}: {exc}"}
    return build


def handler_for(cache: Cache):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, TEMPLATE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/static/lightweight-charts.js":
                self._send(200, CHART_LIB.read_bytes(), "text/javascript; charset=utf-8")
            elif url.path == "/api/snapshot":
                refresh = "refresh" in parse_qs(url.query)
                body = json.dumps(cache.get(refresh), default=str).encode("utf-8")
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, fmt, *args) -> None:
            pass                           # keep the terminal quiet

    return Handler


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--demo", action="store_true", help="invented data, no broker")
    p.add_argument("--no-news", dest="news", action="store_false")
    a = p.parse_args()

    builder = (lambda: demo.build()) if a.demo else live_builder(a.news)
    server = ThreadingHTTPServer((a.host, a.port), handler_for(Cache(builder, CACHE_SECONDS)))
    print(f"desk running at http://{a.host}:{a.port}"
          f"{'  (DEMO DATA)' if a.demo else ''}   Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
