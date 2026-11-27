#!/usr/bin/env python3
"""Local web server for the Prussian dictionary / translator.

    python3 server.py            # http://127.0.0.1:8765
    python3 server.py --port 9000 --host 0.0.0.0

Only the Python standard library is used (http.server + sqlite3).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import random
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from translate import DEFAULT_DB, LANGS, Dictionary

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
DATA = HERE / "data"

DICT: Dictionary


class Handler(BaseHTTPRequestHandler):
    server_version = "PrussianDict/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        if not self.path.startswith(("/api/suggest", "/favicon")):
            super().log_message(fmt, *args)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _send(self, body: bytes, ctype: str = "application/json; charset=utf-8",
              status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), status=status)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status=status)

    def _query(self) -> dict[str, str]:
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q, keep_blank_values=True).items()}

    # ── routes ──────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/search":
                return self.api_search()
            if path == "/api/suggest":
                return self.api_suggest()
            if path == "/api/entry":
                return self.api_entry()
            if path == "/api/gloss":
                return self.api_gloss()
            if path == "/api/random":
                return self.api_random()
            if path == "/api/stats":
                return self._json(DICT.stats())
            if path in ("/", "/index.html"):
                return self._file(WEB / "index.html")
            if path in ("/words.csv", "/forms.csv", "/dictionary.sqlite"):
                return self._file(DATA / path.lstrip("/"))
            return self._file((WEB / path.lstrip("/")).resolve())
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - show a usable message
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _file(self, path: Path) -> None:
        if not path.is_file() or WEB not in path.parents and DATA not in path.parents:
            return self._error("not found", 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(path.read_bytes(), ctype, cache="max-age=3600")

    # ── API ─────────────────────────────────────────────────────────────────
    def api_search(self) -> None:
        q = self._query()
        query = q.get("q", "").strip()
        lang = q.get("lang", "ru")
        limit = min(int(q.get("limit", 40) or 40), 100)
        if not query:
            return self._json({"query": "", "results": []})
        results = DICT.search(query, lang, limit)
        self._json({
            "query": query, "lang": lang,
            "results": [{
                **DICT.entry_payload(r["entry"], r["match"], with_tables=False),
            } for r in results],
        })

    def api_suggest(self) -> None:
        q = self._query()
        self._json({"suggestions": DICT.suggest(q.get("q", ""), q.get("lang", "ru"))})

    def api_entry(self) -> None:
        q = self._query()
        try:
            eid = int(q.get("id", ""))
        except ValueError:
            return self._error("bad id")
        entry = DICT.entries.get(eid)
        if entry is None:
            return self._error("not found", 404)
        self._json(DICT.entry_payload(entry, with_tables=True))

    def api_gloss(self) -> None:
        q = self._query()
        text = q.get("text", "").strip()
        src = q.get("src", "prs")
        dst = q.get("dst", "ru")
        if not text:
            return self._json({"tokens": []})
        if src not in ("prs", *LANGS):
            src = "prs"
        if dst not in LANGS:
            dst = "ru"
        self._json(DICT.gloss(text[:2000], src, dst))

    def api_random(self) -> None:
        e = random.choice(list(DICT.entries.values()))
        self._json(DICT.entry_payload(e, with_tables=True))


def main() -> None:
    global DICT
    ap = argparse.ArgumentParser(description="Prussian dictionary web translator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    print("Loading dictionary …", flush=True)
    DICT = Dictionary(args.db)
    s = DICT.stats()
    print(f"  {s['entries']} entries, {s['forms']} form records, "
          f"{s['unique_forms']} unique forms", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
