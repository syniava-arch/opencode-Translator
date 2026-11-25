#!/usr/bin/env python3
"""Parse the mirrored wirdeins NVH dump into structured JSONL.

Source: https://github.com/strfry/wirdeins (mirror of wirdeins.twanksta.org,
dictionary © Twānkstas Prūsa). The file twanksta/nvh/full.nvh contains every
entry with its six translations and all inflected forms (tagged).

NVH format used here:
    entry: <headword>
      id: <headword>
      pos: noun
      gender: masc
      paradigm: 32
      label: MK
      sense:
        en: abacus
        ru: абак
      inflectedForm: abaks
        tag: sg.nom
      legacy:
        desc: [Abakus Pit]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LANGS = ("en", "de", "lt", "lv", "pl", "ru")

# 2-space top-level fields; 4-space children of sense/legacy; 4-space tag of a form
ENTRY_RE = re.compile(r"^entry:\s*(.*)$")
FIELD_RE = re.compile(r"^  ([A-Za-z][A-Za-z0-9_]*):\s*(.*)$")
CHILD_RE = re.compile(r"^    ([A-Za-z][A-Za-z0-9_]*):\s*(.*)$")


def parse(path: Path) -> list[dict]:
    entries: list[dict] = []
    cur: dict | None = None
    block: str | None = None  # "sense" | "legacy" | None
    last_form: dict | None = None

    def finish() -> None:
        nonlocal cur, block, last_form
        if cur is not None:
            entries.append(cur)
        cur = None
        block = None
        last_form = None

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line.startswith(" "):
                m = ENTRY_RE.match(line)
                if m:
                    finish()
                    cur = {
                        "headword": m.group(1).strip(),
                        "translations": {lang: [] for lang in LANGS},
                        "forms": [],
                        "labels": [],
                        "relations": [],
                        "homographs": [],
                    }
                continue
            if cur is None:
                continue
            m = FIELD_RE.match(line)
            if m:
                name, value = m.group(1), m.group(2).strip()
                block = name if name in ("sense", "legacy") else None
                last_form = None
                if name == "sense":
                    continue
                if name == "legacy":
                    continue
                if name == "id":
                    cur.setdefault("id", value)
                elif name == "pos":
                    cur["pos"] = value
                elif name == "gender":
                    cur["gender"] = value
                elif name == "paradigm":
                    cur["paradigm"] = value
                elif name == "numtype":
                    cur["numtype"] = value
                elif name == "pronunciation":
                    cur["pronunciation"] = value
                elif name == "label":
                    cur["labels"].append(value)
                elif name == "relation":
                    cur["relations"].append(value)
                elif name == "homograph":
                    cur["homographs"].append(value)
                elif name == "inflectedForm":
                    last_form = {"form": value, "tag": None}
                    cur["forms"].append(last_form)
                continue
            m = CHILD_RE.match(line)
            if m:
                name, value = m.group(1), m.group(2).strip()
                if block == "sense" and name in LANGS:
                    if value:
                        cur["translations"][name].append(value)
                elif block == "legacy" and name == "desc":
                    cur["desc"] = value
                elif last_form is not None and name == "tag":
                    last_form["tag"] = value
                continue
    finish()

    # tidy
    for e in entries:
        e["translations"] = {k: v for k, v in e["translations"].items() if v}
        e.pop("labels", None)
        e.pop("relations", None)
        e.pop("homographs", None)
        for f in e["forms"]:
            if f["tag"] is None:
                f.pop("tag")
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("nvh", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()

    entries = parse(args.nvh)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    words = {e["headword"] for e in entries}
    forms = [f["form"] for e in entries for f in e["forms"]]
    with_ru = sum(1 for e in entries if e["translations"].get("ru"))
    with_any = sum(1 for e in entries if e["translations"])
    print(f"entries:            {len(entries)}")
    print(f"unique headwords:   {len(words)}")
    print(f"inflected form refs:{len(forms)}")
    print(f"unique form strings:{len(set(forms))}")
    print(f"with ru translation:{with_ru}")
    print(f"with any translation:{with_any}")
    from collections import Counter

    print("pos:", Counter(e.get("pos", "—") for e in entries).most_common())
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
