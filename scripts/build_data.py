#!/usr/bin/env python3
"""Build the translator dataset from the parsed NVH mirror.

Inputs : data/entries.jsonl (see parse_nvh.py)
Outputs: data/words.csv, data/forms.csv, data/dictionary.sqlite

Cross-reference entries (headwords that only exist as a form of another word,
``desc`` starting with "↑") inherit the translations of their lemma so that a
search for e.g. "galbimai" still shows the meaning of "galbtwei".
"""
from __future__ import annotations

import csv
import html
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

LANGS = ("ru", "en", "de", "lt", "lv", "pl")
TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r'href="#([^"]+)"')
WS_RE = re.compile(r"\s+")

POS_RU = {
    "noun": "сущ.",
    "verb": "глаг.",
    "adj": "прил.",
    "adv": "нареч.",
    "prep": "предлог",
    "num": "числ.",
    "intj": "межд.",
    "sconj": "союз",
    "cconj": "союз",
    "part": "частица",
    "pron": "мест.",
    "postp": "посл.",
}


def clean_desc(value: str) -> str:
    if not value:
        return ""
    value = TAG_RE.sub("", value)
    value = html.unescape(value)
    return WS_RE.sub(" ", value).strip()


def fold(text: str) -> str:
    """Diacritic-insensitive key, like the search on the original site."""
    text = unicodedata.normalize("NFD", text).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Prussian long vowels and a few orthographic variants
    for src, dst in (("ā", "a"), ("ē", "e"), ("ī", "i"), ("ō", "o"), ("ū", "u"),
                     ("ȳ", "y"), ("’", ""), ("'", "")):
        text = text.replace(src, dst)
    return WS_RE.sub(" ", text).strip()


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def load_extras(path: Path, entries: list[dict]) -> list[dict]:
    """Hand-written additions that are not in the upstream NVH dump.

    An extra is skipped if the same headword + part of speech already exists,
    so rebuilding after an upstream update does not create duplicates.
    """
    if not path.exists():
        return []
    existing = {(e["headword"], e.get("pos", "")) for e in entries}
    extras = []
    for e in load(path):
        key = (e["headword"], e.get("pos", ""))
        if key in existing:
            print(f"extra skipped (already present): {e['headword']}")
            continue
        existing.add(key)
        extras.append(e)
    return extras


def resolve_xrefs(entries: list[dict]) -> None:
    by_head: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_head[e["headword"]].append(e)
        by_head[e["headword"].casefold()].append(e)

    for e in entries:
        e["derived_from"] = ""
        desc = e.get("desc", "")
        if e["translations"] or not desc.startswith("↑"):
            continue
        m = HREF_RE.search(desc)
        if not m:
            continue
        target = by_head.get(m.group(1)) or by_head.get(m.group(1).casefold())
        if not target:
            continue
        src = next((t for t in target if t["translations"]), target[0])
        if src is e:
            continue
        e["translations"] = src["translations"]
        e["derived_from"] = src["headword"]
        if "pos" not in e and "pos" in src:
            e["pos"] = src["pos"]
        if "gender" not in e and "gender" in src:
            e["gender"] = src["gender"]


def build(entries_path: Path, out_dir: Path) -> None:
    entries = load(entries_path)
    extras = load_extras(entries_path.parent / "extra_entries.jsonl", entries)
    entries.extend(extras)
    resolve_xrefs(entries)
    out_dir.mkdir(parents=True, exist_ok=True)

    # add stable ids and normalised fields
    for i, e in enumerate(entries, start=1):
        e["eid"] = i
        e["head_norm"] = fold(e["headword"])
        e["desc_clean"] = clean_desc(e.get("desc", ""))
        e.pop("desc", None)
        if "pos" not in e:
            e["pos"] = "phrase" if " " in e["headword"] else ""
        # A few function words are stored without a POS in the NVH; the legacy
        # description starts with the original grammar tag (pn = pronoun, …).
        d0 = e.get("desc_clean", "")
        if d0.startswith("pn ") or d0 == "pn":
            e["pos"] = "pron"
        elif d0.startswith("ij ") or d0 == "ij":
            e["pos"] = "intj"
        elif d0.startswith("cj ") or d0 == "cj":
            e["pos"] = "cconj"
        for f in e["forms"]:
            f["norm"] = fold(f["form"])

    # ── CSV: words ──────────────────────────────────────────────────────────
    with (out_dir / "words.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["id", "headword", "pos", "pos_ru", "gender", "paradigm",
                    "forms", "ru", "en", "de", "lt", "lv", "pl", "notes"])
        for e in entries:
            tr = e["translations"]
            w.writerow([
                e["eid"], e["headword"], e["pos"], POS_RU.get(e["pos"], ""),
                e.get("gender", ""), e.get("paradigm", ""), len(e["forms"]),
                " | ".join(tr.get("ru", [])), " | ".join(tr.get("en", [])),
                " | ".join(tr.get("de", [])), " | ".join(tr.get("lt", [])),
                " | ".join(tr.get("lv", [])), " | ".join(tr.get("pl", [])),
                e["desc_clean"],
            ])

    # ── CSV: forms ──────────────────────────────────────────────────────────
    with (out_dir / "forms.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["entry_id", "headword", "pos", "form", "tag"])
        for e in entries:
            for f in e["forms"]:
                w.writerow([e["eid"], e["headword"], e["pos"], f["form"], f.get("tag", "")])

    # ── SQLite ──────────────────────────────────────────────────────────────
    db_path = out_dir / "dictionary.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY,
            headword TEXT NOT NULL,
            head_norm TEXT NOT NULL,
            pos TEXT NOT NULL DEFAULT '',
            gender TEXT NOT NULL DEFAULT '',
            paradigm TEXT NOT NULL DEFAULT '',
            desc TEXT NOT NULL DEFAULT '',
            derived_from TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE translations (
            entry_id INTEGER NOT NULL REFERENCES entries(id),
            lang TEXT NOT NULL,
            sense INTEGER NOT NULL,
            text TEXT NOT NULL,
            text_norm TEXT NOT NULL
        );
        CREATE TABLE forms (
            entry_id INTEGER NOT NULL REFERENCES entries(id),
            form TEXT NOT NULL,
            form_norm TEXT NOT NULL,
            tag TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX idx_entries_norm ON entries(head_norm);
        CREATE INDEX idx_trans_norm ON translations(lang, text_norm);
        CREATE INDEX idx_forms_norm ON forms(form_norm);
        CREATE INDEX idx_forms_entry ON forms(entry_id);
    """)
    con.executemany(
        "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)",
        [(e["eid"], e["headword"], e["head_norm"], e["pos"], e.get("gender", ""),
          e.get("paradigm", ""), e["desc_clean"], e["derived_from"]) for e in entries],
    )
    con.executemany(
        "INSERT INTO translations VALUES (?,?,?,?,?)",
        [(e["eid"], lang, i, text, fold(text))
         for e in entries for lang in LANGS
         for i, text in enumerate(e["translations"].get(lang, []), start=1)],
    )
    con.executemany(
        "INSERT INTO forms VALUES (?,?,?,?)",
        [(e["eid"], f["form"], f["norm"], f.get("tag", "")) for e in entries for f in e["forms"]],
    )
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("PRAGMA journal_mode=DELETE")
    con.commit()

    n_forms = sum(len(e["forms"]) for e in entries)
    n_unique_forms = len({f["form"] for e in entries for f in e["forms"]})
    n_with_ru = sum(1 for e in entries if e["translations"].get("ru"))
    print(f"entries:             {len(entries)}")
    print(f"extras:              {len(extras)}")
    print(f"unique headwords:    {len({e['headword'] for e in entries})}")
    print(f"entry form records:  {n_forms}")
    print(f"unique word forms:   {n_unique_forms}")
    print(f"entries with ru:     {n_with_ru}")
    print(f"database:            {db_path} ({db_path.stat().st_size/1e6:.1f} MB)")
    con.close()


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    build(here / "data" / "entries.jsonl", here / "data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
