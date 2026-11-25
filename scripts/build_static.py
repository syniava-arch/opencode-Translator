#!/usr/bin/env python3
"""Build a fully static, client-side version of the dictionary in site/.

The site has no server-side code: it loads one compact JSON file and does
search, inflection lookup and glossing in the browser. The JSON is ~8.7 MB
(~1.8 MB gzipped), so it fits GitHub Pages (1 GB site) and Cloudflare Pages
(25 MiB per file) with plenty of room.

    python3 scripts/build_static.py
    cd site && python3 -m http.server 8080
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from translate import Dictionary  # noqa: E402

DOWNLOAD_BLOCK = """      <div class="dl">
        <a href="/words.csv" download>words.csv — слова и переводы</a>
        <a href="/forms.csv" download>forms.csv — словоформы</a>
        <a href="/dictionary.sqlite" download>dictionary.sqlite — база</a>
      </div>"""

STATIC_DOWNLOAD_BLOCK = """      <div class="dl">
        <a href="data/dictionary.json" download>dictionary.json — все данные сайта (JSON)</a>
      </div>
      <p style="font-size:12.5px;color:var(--muted)">CSV и SQLite доступны в локальной версии проекта.</p>"""


def build_payload() -> dict:
    d = Dictionary()
    tags: list[str] = []
    tag_idx: dict[str, int] = {}
    entries = []
    for e in d.entries.values():
        fs = []
        for form, tag in e.forms:
            i = tag_idx.get(tag)
            if i is None:
                i = len(tags)
                tag_idx[tag] = i
                tags.append(tag)
            fs.append([form, i])
        entries.append([e.id, e.headword, e.pos, e.gender, e.paradigm,
                        e.desc, e.derived_from,
                        {k: v for k, v in e.translations.items()}, fs])

    meta = d.stats()
    meta["source"] = "wirdeins.twanksta.org (mirror strfry/wirdeins, 2026-11-22)"
    return {"meta": meta, "tags": tags, "entries": entries}


def render_html() -> str:
    html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    if DOWNLOAD_BLOCK not in html:
        raise SystemExit("index.html: download block not found — update build_static.py")
    html = html.replace(DOWNLOAD_BLOCK, STATIC_DOWNLOAD_BLOCK)
    marker = "<script>\nconst LANGS"
    if marker not in html:
        raise SystemExit("index.html: inline script marker not found")
    # app.js must run before the UI script, because it replaces window.fetch
    html = html.replace(marker, '<script src="app.js"></script>\n' + marker, 1)
    return html


def main() -> int:
    payload = build_payload()

    site = HERE / "site"
    data = site / "data"
    data.mkdir(parents=True, exist_ok=True)

    out = data / "dictionary.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    (site / "index.html").write_text(render_html(), encoding="utf-8")
    shutil.copy2(HERE / "web" / "localapi.js", site / "app.js")
    (site / ".nojekyll").write_text("", encoding="utf-8")

    size = out.stat().st_size
    print(f"site:    {site}")
    print(f"data:    {out}  ({size/1e6:.2f} MB, gzip ~1.8 MB)")
    print(f"tags:    {len(payload['tags'])}")
    print(f"entries: {len(payload['entries'])}")
    print("every file < 25 MiB:", size < 25 * 1024 * 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
