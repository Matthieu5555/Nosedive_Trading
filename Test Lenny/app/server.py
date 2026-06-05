from __future__ import annotations

import hashlib
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from analytics import (
    OrderTicket,
    build_surface_points,
    quality_report,
    risk_summary,
    utc_now_iso,
)
from providers import DemoProvider, IbkrProvider, MarketDataProvider
from storage import LocalStore


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
RUNTIME = ROOT / "runtime"
CODE_VERSION = "test-lenny-v1"


def config_hash() -> str:
    fields = {
        "mode": os.getenv("IBKR_MODE", "demo"),
        "host": os.getenv("IBKR_HOST", "127.0.0.1"),
        "port": os.getenv("IBKR_PORT", "7497"),
        "client_id": os.getenv("IBKR_CLIENT_ID", "71"),
        "orders": os.getenv("IBKR_ENABLE_ORDERS", "false"),
    }
    payload = json.dumps(fields, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def build_provider() -> MarketDataProvider:
    mode = os.getenv("IBKR_MODE", "demo").lower()
    if mode == "ibkr":
        return IbkrProvider(
            host=os.getenv("IBKR_HOST", "127.0.0.1"),
            port=int(os.getenv("IBKR_PORT", "7497")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "71")),
            enable_orders=os.getenv("IBKR_ENABLE_ORDERS", "false").lower() == "true",
        )
    return DemoProvider()


class AppState:
    def __init__(self) -> None:
        self.provider = build_provider()
        self.store = LocalStore(RUNTIME / "test_lenny.sqlite")


STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/api/status":
            self._json(
                {
                    "status": "ok",
                    "mode": os.getenv("IBKR_MODE", "demo"),
                    "codeVersion": CODE_VERSION,
                    "configHash": config_hash(),
                    "rawEvents": STATE.store.raw_count(),
                    "ordersEnabled": os.getenv("IBKR_ENABLE_ORDERS", "false").lower() == "true",
                }
            )
            return
        if parsed.path == "/api/snapshot":
            symbols = parse_symbols(parsed.query)
            self._snapshot(symbols)
            return
        if parsed.path == "/api/risk":
            symbols = parse_symbols(parsed.query)
            snapshot = STATE.provider.snapshot(symbols)
            surfaces = build_surface_points(snapshot.underlyings, snapshot.options)
            report = risk_summary(snapshot.positions, snapshot.options)
            STATE.store.append_event(snapshot.timestamp, "risk_view", report)
            self._json({"risk": report, "quality": quality_report(snapshot.underlyings, snapshot.options, surfaces)})
            return
        if parsed.path == "/api/orders":
            self._json({"orders": STATE.store.recent_orders()})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_json()
        if parsed.path == "/api/orders/preview":
            ticket = normalize_ticket(body)
            result = STATE.provider.preview_order(ticket.__dict__)
            STATE.store.append_order(utc_now_iso(), "preview", result)
            self._json(result)
            return
        if parsed.path == "/api/orders/place":
            ticket = normalize_ticket(body)
            result = STATE.provider.place_order(ticket.__dict__)
            STATE.store.append_order(utc_now_iso(), str(result.get("status", "unknown")), result)
            self._json(result)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _snapshot(self, symbols: list[str]) -> None:
        try:
            snapshot = STATE.provider.snapshot(symbols)
            surfaces = build_surface_points(snapshot.underlyings, snapshot.options)
            quality = quality_report(snapshot.underlyings, snapshot.options, surfaces)
            payload = snapshot.as_json()
            payload["surfaces"] = [point.__dict__ for point in surfaces]
            payload["quality"] = quality
            payload["provenance"] = {
                "codeVersion": CODE_VERSION,
                "configHash": config_hash(),
                "source": snapshot.mode,
                "calcTs": utc_now_iso(),
            }
            STATE.store.append_event(snapshot.timestamp, "market_snapshot", payload)
            self._json(payload)
        except Exception as exc:
            self._json({"error": str(exc), "mode": os.getenv("IBKR_MODE", "demo")}, HTTPStatus.BAD_GATEWAY)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_static(self, relative: str) -> None:
        path = (STATIC / relative).resolve()
        if not str(path).startswith(str(STATIC.resolve())) or not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = "text/html"
        if path.suffix == ".css":
            content_type = "text/css"
        if path.suffix == ".js":
            content_type = "text/javascript"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{utc_now_iso()} http {fmt % args}")


def parse_symbols(query: str) -> list[str]:
    values = parse_qs(query).get("symbols", ["SPY,QQQ,IWM,DIA,VIX"])[0]
    return [symbol.strip().upper() for symbol in values.split(",") if symbol.strip()]


def normalize_ticket(body: dict[str, object]) -> OrderTicket:
    symbol = str(body.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    action = str(body.get("action", "BUY")).upper()
    if action not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")
    quantity = int(body.get("quantity", 1))
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    order_type = str(body.get("orderType", "LMT")).upper()
    limit_price_raw = body.get("limitPrice")
    limit_price = None if limit_price_raw in (None, "") else float(limit_price_raw)
    if order_type == "LMT" and limit_price is None:
        raise ValueError("limitPrice is required for LMT orders")
    transmit = bool(body.get("transmit", False))
    return OrderTicket(
        symbol=symbol,
        action=action,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        transmit=transmit,
    )


def main() -> None:
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Test Lenny running at http://{host}:{port}")
    print("IBKR paper login happens in TWS/IB Gateway, not in this app.")
    server.serve_forever()


if __name__ == "__main__":
    main()
