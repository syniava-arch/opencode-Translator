#!/usr/bin/env python3
"""Prussian dictionary / translator core.

Data: data/dictionary.sqlite, built from the NVH mirror of
wirdeins.twanksta.org (dictionary © Twānkstas Prūsa, GPL-compatible mirror
at https://github.com/strfry/wirdeins).

The dictionary is bidirectional: Prussian headwords and their inflected forms
on one side, translations in Russian, English, German, Lithuanian, Latvian and
Polish on the other.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "dictionary.sqlite"

LANGS = {
    "ru": "Русский",
    "en": "English",
    "de": "Deutsch",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "pl": "Polski",
}

POS_RU = {
    "noun": "существительное",
    "verb": "глагол",
    "adj": "прилагательное",
    "adv": "наречие",
    "prep": "предлог",
    "num": "числительное",
    "intj": "междометие",
    "sconj": "союз",
    "cconj": "союз",
    "part": "частица",
    "pron": "местоимение",
    "postp": "послелог",
    "phrase": "выражение",
    "": "—",
}

GENDER_RU = {"masc": "муж.", "fem": "жен.", "neut": "ср.",
             "msc": "муж.", "neu": "ср."}

CASE_RU = {"nom": "Именительный", "gen": "Родительный",
           "dat": "Дательный", "acc": "Винительный"}

PERSON_RU = {"sg1": "я", "sg2": "ты", "sp3": "он / она / оно",
             "pl1": "мы", "pl2": "вы", "sp3p": "они"}

TENSE_RU = {"prs": "Настоящее", "prt": "Прошедшее", "subj": "Сослагательное"}

PART_RU = {
    "part.prs.act": "Причастие настоящего времени, действительное",
    "part.prf.act": "Причастие прошедшего времени, действительное",
    "part.prf.pss": "Причастие прошедшего времени, страдательное",
}

TAG_LABELS = {
    "imprt.sg2": "Повелительное, ед. ч. (ты)",
    "imprt.pl2": "Повелительное, мн. ч. (вы)",
    "opt": "Оптатив",
    "adv": "Наречие",
    "adv.comp": "Наречие, сравн.",
    "adv.superl": "Наречие, превосх.",
}

# fixed pronouns of the paradigm tables (the 3rd person is fused in Prussian)
VERB_ROWS = [
    ("as", "sg1"), ("tū", "sg2"), ("tāns/tenā/tennan", "sp3"),
    ("mes", "pl1"), ("jūs", "pl2"), ("tenēi/tennas", "sp3"),
]
PERIPHRASTIC = [
    ("as", "asma", "wīrst", ("msc.sg", "fem.sg")),
    ("tū", "assei", "wīrst", ("msc.sg", "fem.sg")),
    ("tāns/tenā", "ast", "wīrst", ("msc.sg", "fem.sg")),
    ("tennan", "ast", "wīrst", ("neu.sg",)),
    ("mes", "asmai", "wīrstmai", ("msc.pl", "fem.pl")),
    ("jūs", "astei", "wīrstei", ("msc.pl", "fem.pl")),
    ("tenēi/tennas", "ast", "wīrst", ("msc.pl", "fem.pl")),
]
GENDERS = ("msc", "fem", "neu")

TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)


def fold(text: str) -> str:
    """Diacritic-insensitive search key (ā→a, ū→u, … + casefold)."""
    text = unicodedata.normalize("NFD", text).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for src, dst in (("ā", "a"), ("ē", "e"), ("ī", "i"), ("ō", "o"), ("ū", "u"),
                     ("ȳ", "y"), ("’", "'")):
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Match:
    kind: str            # headword | form | translation
    text: str = ""
    tag: str = ""
    lang: str = ""
    score: int = 0


@dataclass
class Entry:
    id: int
    headword: str
    norm: str
    pos: str
    gender: str
    paradigm: str
    desc: str
    derived_from: str
    translations: dict[str, list[str]] = field(default_factory=dict)
    forms: list[tuple[str, str]] = field(default_factory=list)  # (form, tag)

    def tr(self, lang: str) -> list[str]:
        return self.translations.get(lang, [])

    @property
    def pos_ru(self) -> str:
        return POS_RU.get(self.pos, self.pos or "—")


class Dictionary:
    def __init__(self, db_path: Path | str = DEFAULT_DB) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"dictionary not found: {self.db_path}")
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        self.entries: dict[int, Entry] = {}
        for row in con.execute("SELECT * FROM entries"):
            self.entries[row["id"]] = Entry(
                id=row["id"], headword=row["headword"], norm=row["head_norm"],
                pos=row["pos"], gender=row["gender"], paradigm=row["paradigm"],
                desc=row["desc"], derived_from=row["derived_from"])
        for row in con.execute("SELECT entry_id, lang, text FROM translations ORDER BY sense"):
            self.entries[row["entry_id"]].translations.setdefault(row["lang"], []).append(row["text"])
        for row in con.execute("SELECT entry_id, form, tag FROM forms"):
            self.entries[row["entry_id"]].forms.append((row["form"], row["tag"]))
        con.close()

        self.by_head: dict[str, list[int]] = {}
        self.by_form: dict[str, list[tuple[int, str, str]]] = {}
        self.by_trans: dict[str, dict[str, list[tuple[int, str]]]] = {l: {} for l in LANGS}
        for e in self.entries.values():
            self.by_head.setdefault(e.norm, []).append(e.id)
            for form, tag in e.forms:
                self.by_form.setdefault(fold(form), []).append((e.id, form, tag))
            for lang, texts in e.translations.items():
                for text in texts:
                    self.by_trans.setdefault(lang, {}).setdefault(fold(text), []).append((e.id, text))
        # sorted keys for prefix scans
        self._head_keys = sorted(self.by_head)
        self._trans_keys = {l: sorted(m) for l, m in self.by_trans.items()}

    # ── search ───────────────────────────────────────────────────────────────
    @staticmethod
    def _prefix_keys(keys: list[str], prefix: str, limit: int = 200) -> list[str]:
        import bisect
        i = bisect.bisect_left(keys, prefix)
        out = []
        while i < len(keys) and keys[i].startswith(prefix) and len(out) < limit:
            out.append(keys[i])
            i += 1
        return out

    def search(self, query: str, lang: str = "ru", limit: int = 40) -> list[dict]:
        q = fold(query)
        if not q:
            return []
        if lang not in LANGS:
            lang = "ru"
        best: dict[int, Match] = {}

        def add(eid: int, score: int, match: Match) -> None:
            old = best.get(eid)
            if old is None or score > old.score:
                match.score = score
                best[eid] = match

        # 1. exact headword
        head_hits = self.by_head.get(q, ())
        for eid in head_hits:
            add(eid, 1000, Match("headword", self.entries[eid].headword))
        # 2. exact inflected form (pronoun paradigms duplicate each other:
        #    "as" is listed both as its own entry and as a form of "mes", skip that)
        pron_head = any(self.entries[eid].pos == "pron" for eid in head_hits)
        for eid, form, tag in self.by_form.get(q, ()):
            if pron_head and self.entries[eid].pos == "pron":
                continue
            add(eid, 900, Match("form", form, tag))
        # 3. exact translation in the selected language (other languages only as
        #    a fallback when the selected one yields too little)
        for eid, text in self.by_trans.get(lang, {}).get(q, ()):
            add(eid, 800, Match("translation", text, lang=lang))
        # 4. headword prefix
        for key in self._prefix_keys(self._head_keys, q):
            for eid in self.by_head[key]:
                add(eid, 700, Match("headword", self.entries[eid].headword))
        # 5. translation prefix
        for key in self._prefix_keys(self._trans_keys.get(lang, []), q):
            for eid, text in self.by_trans[lang][key]:
                add(eid, 550, Match("translation", text, lang=lang))
        # 6. inflected form prefix
        if len(q) >= 2:
            for key in self._prefix_keys(sorted(self.by_form), q):
                for eid, form, tag in self.by_form[key]:
                    add(eid, 400, Match("form", form, tag))
        # 7. substring in headword / translation / form
        if len(q) >= 2:
            for e in self.entries.values():
                if q in e.norm and e.norm != q:
                    add(e.id, 300, Match("headword", e.headword))
            for key, hits in self.by_trans.get(lang, {}).items():
                if q in key and key != q:
                    for eid, text in hits:
                        add(eid, 250, Match("translation", text, lang=lang))
        if len(q) >= 3:
            for key, hits in self.by_form.items():
                if q in key and key != q:
                    for eid, form, tag in hits:
                        add(eid, 150, Match("form", form, tag))

        # last resort: the same query in the other five languages
        if not best:
            for l in (x for x in LANGS if x != lang):
                for eid, text in self.by_trans.get(l, {}).get(q, ()):
                    add(eid, 350, Match("translation", text, lang=l))
                for key in self._prefix_keys(self._trans_keys.get(l, []), q, 50):
                    for eid, text in self.by_trans[l][key]:
                        add(eid, 200, Match("translation", text, lang=l))

        results = []
        for eid, match in sorted(best.items(), key=lambda kv: (-kv[1].score, self.entries[kv[0]].headword)):
            results.append({"entry": self.entries[eid], "match": match})
            if len(results) >= limit:
                break
        return results

    def suggest(self, prefix: str, lang: str = "ru", limit: int = 10) -> list[dict]:
        q = fold(prefix)
        if not q:
            return []
        out, seen = [], set()
        for key in self._prefix_keys(self._head_keys, q, limit * 3):
            for eid in self.by_head[key]:
                if eid not in seen:
                    seen.add(eid)
                    out.append({"word": self.entries[eid].headword,
                                "translation": " · ".join(self.entries[eid].tr(lang)[:2]),
                                "pos": self.entries[eid].pos})
        if len(out) < limit:
            for l in (lang, *(x for x in LANGS if x != lang)):
                for key in self._prefix_keys(self._trans_keys.get(l, []), q, limit * 3):
                    for eid, text in self.by_trans[l][key]:
                        e = self.entries[eid]
                        if eid not in seen:
                            seen.add(eid)
                            out.append({"word": e.headword,
                                        "translation": " · ".join(e.tr(lang)[:2]),
                                        "pos": e.pos})
                        if len(out) >= limit:
                            break
                    if len(out) >= limit:
                        break
                if len(out) >= limit:
                    break
        return out[:limit]

    # ── paradigms ────────────────────────────────────────────────────────────
    def paradigm(self, entry: Entry) -> dict:
        cells: dict[str, list[str]] = {}
        for form, tag in entry.forms:
            cells.setdefault(tag, [])
            if form not in cells[tag]:
                cells[tag].append(form)

        def cell(tag: str) -> str:
            return " / ".join(cells.get(tag, ()))

        if entry.pos == "noun" and cells:
            return {"type": "noun", "tables": [{
                "title": None, "kind": "cases",
                "corner": "Падеж",
                "columns": ["Единственное", "Множественное"],
                "rows": [{"label": CASE_RU[c], "cells": [cell(f"sg.{c}"), cell(f"pl.{c}")]}
                         for c in ("nom", "gen", "dat", "acc")]}]}

        if entry.pos in ("adj", "num", "pron") and cells:
            tables = []
            for g in GENDERS:
                if not any(t.startswith(g + ".") for t in cells):
                    continue
                tables.append({
                    "title": GENDER_RU[g], "kind": "cases", "corner": "Падеж",
                    "columns": ["Единственное", "Множественное"],
                    "rows": [{"label": CASE_RU[c],
                              "cells": [cell(f"{g}.sg.{c}"), cell(f"{g}.pl.{c}")]}
                             for c in ("nom", "gen", "dat", "acc")]})
            for prefix, title in (("comp", "Сравнительная степень"),
                                  ("superl", "Превосходная степень")):
                rows = []
                for g in GENDERS:
                    for n, nlabel in (("sg", "ед."), ("pl", "мн.")):
                        for c in ("nom", "gen", "dat", "acc"):
                            v = cell(f"{prefix}.{g}.{n}.{c}")
                            if v:
                                rows.append({"label": f"{GENDER_RU[g]} {nlabel} {CASE_RU[c].lower()}",
                                             "cells": [v]})
                if rows:
                    tables.append({"title": title, "kind": "list",
                                   "columns": ["Форма"],
                                   "rows": rows})
            adv = cell("adv")
            if adv:
                tables.append({"title": "Наречие", "kind": "list", "columns": ["Форма"],
                               "rows": [{"label": "положительная", "cells": [adv]},
                                        {"label": "сравнительная", "cells": [cell("adv.comp")]},
                                        {"label": "превосходная", "cells": [cell("adv.superl")]}]})
            return {"type": "adj", "tables": tables}

        if entry.pos == "verb" and cells:
            finite_rows = []
            for pronoun, pn in VERB_ROWS:
                vals = [cell(f"{t}.{pn}") for t in ("prs", "prt", "subj")]
                if any(vals):
                    finite_rows.append({"label": pronoun, "cells": vals})
            tables = [{
                "title": "Личные формы", "kind": "verb", "corner": "Лицо",
                "columns": [TENSE_RU["prs"], TENSE_RU["prt"], TENSE_RU["subj"]],
                "rows": finite_rows}]

            extra = []
            if cell("imprt.sg2") or cell("imprt.pl2"):
                extra.append({"label": "Повелительное наклонение",
                              "cells": [cell("imprt.sg2"), cell("imprt.pl2")]})
            if cell("opt"):
                extra.append({"label": "Оптатив", "cells": [cell("opt")]})
            if extra:
                tables.append({"title": "Наклонения", "kind": "verb", "corner": "",
                               "columns": ["Ед. ч. (ты)", "Мн. ч. (вы)"],
                               "rows": extra})

            # analytic perfect / future (as shown on wirdeins)
            for title, idx in (("Перфект (сложное прошедшее)", 1),
                               ("Футурум (сложное будущее)", 2)):
                rows = []
                for row in PERIPHRASTIC:
                    part = " / ".join(cell(f"part.prf.act.{gn}.nom") for gn in row[3])
                    if part:
                        rows.append({"label": row[0], "cells": [f"{row[idx]} {part}"]})
                if rows:
                    tables.append({"title": title, "kind": "list", "columns": ["Форма"],
                                   "rows": rows})

            for prefix, title in PART_RU.items():
                if not any(t.startswith(prefix + ".") for t in cells):
                    continue
                for g in GENDERS:
                    if not any(t.startswith(f"{prefix}.{g}.") for t in cells):
                        continue
                    tables.append({
                        "title": f"{title} — {GENDER_RU[g]}", "kind": "cases",
                        "corner": "Падеж",
                        "columns": ["Единственное", "Множественное"],
                        "rows": [{"label": CASE_RU[c],
                                  "cells": [cell(f"{prefix}.{g}.sg.{c}"),
                                            cell(f"{prefix}.{g}.pl.{c}")]}
                                 for c in ("nom", "gen", "dat", "acc")]})
            return {"type": "verb", "tables": tables}

        # everything else / invariable
        rows = [{"label": self.tag_label(tag), "cells": [cell(tag)]} for tag in cells]
        return {"type": "other", "tables": ([{"title": None, "kind": "list",
                                              "columns": ["Форма"], "rows": rows}] if rows else [])}

    @staticmethod
    def tag_label(tag: str) -> str:
        if not tag:
            return ""
        if tag in TAG_LABELS:
            return TAG_LABELS[tag]
        parts = tag.split(".")

        def case(c: str) -> str:
            return CASE_RU.get(c, c).lower()

        def num(n: str) -> str:
            return {"sg": "ед. ч.", "pl": "мн. ч."}.get(n, n)

        def gen(g: str) -> str:
            return {"msc": "муж. р.", "fem": "жен. р.", "neu": "ср. р."}.get(g, g)

        # finite verb: prs.sg1, prt.pl2, subj.sp3
        if parts[0] in TENSE_RU and len(parts) == 2 and parts[1] in PERSON_RU:
            tense = {"prs": "наст. вр.", "prt": "прош. вр.", "subj": "сослаг. накл."}[parts[0]]
            person = {"sg1": "1-е л., ед. ч.", "sg2": "2-е л., ед. ч.",
                      "sp3": "3-е л., ед./мн. ч.", "pl1": "1-е л., мн. ч.",
                      "pl2": "2-е л., мн. ч."}.get(parts[1], parts[1])
            return f"{tense}, {person}"
        # imperative / optative
        if parts[0] == "imprt" and len(parts) == 2:
            return "повелительное, " + ("ед. ч. (ты)" if parts[1] == "sg2" else "мн. ч. (вы)")
        # noun: sg.nom
        if parts[0] in ("sg", "pl") and len(parts) == 2 and parts[1] in CASE_RU:
            return f"{num(parts[0])}, {case(parts[1])}"
        # adjective / numeral / pronoun: [comp|superl].msc.sg.nom
        if parts[0] in ("msc", "fem", "neu") and len(parts) == 3:
            return f"{gen(parts[0])}, {num(parts[1])}, {case(parts[2])}"
        if parts[0] in ("comp", "superl") and len(parts) == 4:
            deg = "сравн." if parts[0] == "comp" else "превосх."
            return f"{deg} степень, {gen(parts[1])}, {num(parts[2])}, {case(parts[3])}"
        # participle: part.prf.act.msc.sg.gen
        if parts[0] == "part" and len(parts) >= 6:
            kind = ".".join(parts[:3])
            label = PART_RU.get(kind, kind)
            return f"{label}, {gen(parts[3])}, {num(parts[4])}, {case(parts[5])}"
        return tag

    # ── translation / glossing ───────────────────────────────────────────────
    def translate_word(self, word: str, src: str = "prs", dst: str = "ru",
                       limit: int = 8) -> dict:
        """Single-word translation, either Prussian→X or X→Prussian."""
        if src == "prs":
            return {"direction": "prs→" + dst, "query": word,
                    "results": self._gloss_token(word, dst, limit)}
        # translation → Prussian
        q = fold(word)
        hits = self.by_trans.get(src, {}).get(q, [])
        out = []
        for eid, text in hits[:limit]:
            e = self.entries[eid]
            out.append({"headword": e.headword, "pos": e.pos, "pos_ru": e.pos_ru,
                        "gender": e.gender, "matched": text,
                        "translations": e.translations, "id": e.id})
        return {"direction": src + "→prs", "query": word, "results": out}

    def _gloss_token(self, word: str, dst: str, limit: int = 3) -> list[dict]:
        q = fold(word)
        hits: list[tuple[int, str, str]] = []  # (eid, kind, detail)
        seen: set[int] = set()

        def push(eid: int, kind: str, tag: str) -> None:
            if eid not in seen:
                seen.add(eid)
                hits.append((eid, kind, tag))

        for eid in self.by_head.get(q, ()):
            push(eid, "headword", "")
        pron_head = any(self.entries[eid].pos == "pron" for eid in self.by_head.get(q, ()))
        for eid, form, tag in self.by_form.get(q, ()):
            if pron_head and self.entries[eid].pos == "pron":
                continue
            push(eid, "form", tag)
        if not hits:
            for key in self._prefix_keys(self._head_keys, q, limit):
                for eid in self.by_head[key]:
                    push(eid, "headword", "")
        out = []
        for eid, kind, tag in hits[:limit]:
            e = self.entries[eid]
            out.append({
                "headword": e.headword, "id": e.id, "kind": kind, "tag": tag,
                "tag_label": self.tag_label(tag),
                "pos": e.pos, "pos_ru": e.pos_ru, "gender": e.gender,
                "translation": e.tr(dst), "all_translations": e.translations,
            })
        return out

    def gloss(self, text: str, src: str = "prs", dst: str = "ru") -> dict:
        tokens = TOKEN_RE.findall(text)
        out = []
        pos = 0
        for token in TOKEN_RE.finditer(text):
            if token.start() > pos:
                out.append({"token": text[pos:token.start()], "space": True})
            t = token.group(0)
            out.append({"token": t, "space": False,
                        "analysis": self.translate_word(t, src, dst, limit=3)["results"]})
            pos = token.end()
        if pos < len(text):
            out.append({"token": text[pos:], "space": True})
        return {"text": text, "src": src, "dst": dst, "tokens": out}

    # ── misc ────────────────────────────────────────────────────────────────
    def entry_payload(self, entry: Entry, match: Match | None = None,
                      with_tables: bool = True) -> dict:
        payload = {
            "id": entry.id,
            "headword": entry.headword,
            "pos": entry.pos,
            "pos_ru": entry.pos_ru,
            "gender": entry.gender,
            "gender_ru": GENDER_RU.get(entry.gender, ""),
            "paradigm": entry.paradigm,
            "translations": entry.translations,
            "desc": entry.desc,
            "derived_from": entry.derived_from,
            "forms_count": len(entry.forms),
            "match": None if match is None else {
                "kind": match.kind, "text": match.text, "tag": match.tag,
                "tag_label": self.tag_label(match.tag) if match.tag else "",
                "lang": match.lang},
        }
        if with_tables:
            payload["paradigm_tables"] = self.paradigm(entry)
        return payload

    def stats(self) -> dict:
        forms = [f for e in self.entries.values() for f, _ in e.forms]
        return {
            "entries": len(self.entries),
            "headwords": len({e.headword for e in self.entries.values()}),
            "forms": len(forms),
            "unique_forms": len(set(forms)),
            "unique_forms_norm": len(self.by_form),
            "translations": sum(len(t) for e in self.entries.values() for t in e.translations.values()),
            "with_ru": sum(1 for e in self.entries.values() if e.tr("ru")),
            "with_en": sum(1 for e in self.entries.values() if e.tr("en")),
        }


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Prussian dictionary / translator")
    ap.add_argument("query", nargs="+", help="word or phrase")
    ap.add_argument("--lang", default="ru", choices=list(LANGS))
    ap.add_argument("--from", dest="src", default="prs", choices=["prs", *LANGS])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    d = Dictionary(args.db)
    q = " ".join(args.query)
    if args.src == "prs":
        res = d.search(q, args.lang)
        payload = [d.entry_payload(r["entry"], r["match"]) for r in res]
    else:
        payload = d.translate_word(q, args.src, "ru")["results"]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if not payload:
        print("Ничего не найдено.")
        return 1
    for p in payload:
        m = p.get("match") or {}
        kind = {"headword": "словарная форма", "form": "форма слова",
                "translation": "перевод"}.get(m.get("kind"), "")
        extra = f"  [{kind}: {m.get('text')} {m.get('tag_label','')}]" if kind else ""
        print(f"\n{p['headword']}  —  {p['pos_ru']}"
              + (f", {p['gender_ru']}" if p.get('gender_ru') else "") + extra)
        for lang, texts in p["translations"].items():
            print(f"   {lang}: " + "; ".join(texts))
        if p.get("desc"):
            print(f"   {p['desc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
