/* Static, client-side implementation of the /api/* endpoints.
 *
 * The UI (index.html) is unchanged: it calls fetch("/api/search?...") etc.
 * This script loads data/dictionary.json, builds the indexes in memory and
 * intercepts those requests, so the whole dictionary works from a plain
 * static host (GitHub Pages, Cloudflare Pages, any web server).
 */
(() => {
  "use strict";

  const DATA_URL = "data/dictionary.json?v=382ff1d3e8";
  const LANGS = ["ru", "en", "de", "lt", "lv", "pl"];
  const LANGSET = new Set(LANGS);

  const POS_RU = {
    noun: "существительное", verb: "глагол", adj: "прилагательное",
    adv: "наречие", prep: "предлог", num: "числительное",
    intj: "междометие", sconj: "союз", cconj: "союз", part: "частица",
    pron: "местоимение", postp: "послелог", phrase: "выражение", "": "—",
  };
  const GENDER_RU = { masc: "муж.", fem: "жен.", neut: "ср.", msc: "муж.", neu: "ср." };
  const CASE_RU = { nom: "Именительный", gen: "Родительный", dat: "Дательный", acc: "Винительный" };
  const TENSE_RU = { prs: "Настоящее", prt: "Прошедшее", subj: "Сослагательное" };
  const PART_RU = {
    "part.prs.act": "Причастие настоящего времени, действительное",
    "part.prf.act": "Причастие прошедшего времени, действительное",
    "part.prf.pss": "Причастие прошедшего времени, страдательное",
  };
  const TAG_LABELS = {
    "imprt.sg2": "Повелительное, ед. ч. (ты)",
    "imprt.pl2": "Повелительное, мн. ч. (вы)",
    opt: "Оптатив",
    adv: "Наречие",
    "adv.comp": "Наречие, сравн.",
    "adv.superl": "Наречие, превосх.",
  };
  const VERB_ROWS = [
    ["as", "sg1"], ["tū", "sg2"], ["tāns/tenā/tennan", "sp3"],
    ["mes", "pl1"], ["jūs", "pl2"], ["tenēi/tennas", "sp3"],
  ];
  const PERIPHRASTIC = [
    ["as", "asma", "wīrst", ["msc.sg", "fem.sg"]],
    ["tū", "assei", "wīrst", ["msc.sg", "fem.sg"]],
    ["tāns/tenā", "ast", "wīrst", ["msc.sg", "fem.sg"]],
    ["tennan", "ast", "wīrst", ["neu.sg"]],
    ["mes", "asmai", "wīrstmai", ["msc.pl", "fem.pl"]],
    ["jūs", "astei", "wīrstei", ["msc.pl", "fem.pl"]],
    ["tenēi/tennas", "ast", "wīrst", ["msc.pl", "fem.pl"]],
  ];
  const GENDERS = ["msc", "fem", "neu"];

  function fold(text) {
    let s = String(text ?? "").normalize("NFD").toLowerCase();
    s = s.replace(/[\u0300-\u036f]/g, "");
    s = s.replace(/[’]/g, "'");
    return s.replace(/\s+/g, " ").trim();
  }

  function tagLabel(tag) {
    if (!tag) return "";
    if (TAG_LABELS[tag]) return TAG_LABELS[tag];
    const p = tag.split(".");
    const cat = c => (CASE_RU[c] || c).toLowerCase();
    const num = n => ({ sg: "ед. ч.", pl: "мн. ч." }[n] || n);
    const gen = g => ({ msc: "муж. р.", fem: "жен. р.", neu: "ср. р." }[g] || g);

    if (TENSE_RU[p[0]] && p.length === 2 && ["sg1", "sg2", "sp3", "pl1", "pl2"].includes(p[1])) {
      const tense = { prs: "наст. вр.", prt: "прош. вр.", subj: "сослаг. накл." }[p[0]];
      const person = {
        sg1: "1-е л., ед. ч.", sg2: "2-е л., ед. ч.", sp3: "3-е л., ед./мн. ч.",
        pl1: "1-е л., мн. ч.", pl2: "2-е л., мн. ч.",
      }[p[1]];
      return `${tense}, ${person}`;
    }
    if (p[0] === "imprt" && p.length === 2)
      return "повелительное, " + (p[1] === "sg2" ? "ед. ч. (ты)" : "мн. ч. (вы)");
    if ((p[0] === "sg" || p[0] === "pl") && p.length === 2 && CASE_RU[p[1]])
      return `${num(p[0])}, ${cat(p[1])}`;
    if (["msc", "fem", "neu"].includes(p[0]) && p.length === 3)
      return `${gen(p[0])}, ${num(p[1])}, ${cat(p[2])}`;
    if ((p[0] === "comp" || p[0] === "superl") && p.length === 4)
      return `${p[0] === "comp" ? "сравн." : "превосх."} степень, ${gen(p[1])}, ${num(p[2])}, ${cat(p[3])}`;
    if (p[0] === "part" && p.length >= 6)
      return `${PART_RU[p.slice(0, 3).join(".")] || p.slice(0, 3).join(".")}, ${gen(p[3])}, ${num(p[4])}, ${cat(p[5])}`;
    return tag;
  }

  // Keep the real fetch before we replace it; load() needs it right away.
  const realFetch = window.fetch.bind(window);
  window.__realFetch = realFetch;

  let DB = null;
  const ready = load();

  // thin progress bar while the data file is downloading and indexing
  const boot = document.createElement("div");
  boot.style.cssText = "position:fixed;top:0;left:0;height:3px;width:0;background:#9a4f1f;z-index:99999;transition:width .3s ease";
  (document.body || document.documentElement).appendChild(boot);
  ready.then(
    () => { boot.style.width = "100%"; setTimeout(() => boot.remove(), 350); },
    () => { boot.style.background = "#b3261e"; boot.style.width = "100%"; },
  );

  async function load() {
    const res = await realFetch(DATA_URL, { cache: "force-cache" });
    if (!res.ok) throw new Error(`Не удалось загрузить ${DATA_URL}: ${res.status}`);
    const payload = await res.json();
    const tags = payload.tags;
    const entries = payload.entries.map(a => ({
      id: a[0], h: a[1], hn: fold(a[1]), pos: a[2] || "", gender: a[3] || "",
      par: a[4] || "", desc: a[5] || "", derived: a[6] || "", t: a[7] || {},
      forms: a[8].map(([f, ti]) => [f, tags[ti] || ""]),
    }));

    const byId = new Map();
    const headMap = new Map();
    const formMap = new Map();
    const transMap = {};     // lang -> Map(norm -> [[entry, text]])
    const transKeys = {};
    for (const l of LANGS) { transMap[l] = new Map(); transKeys[l] = []; }

    for (const e of entries) {
      byId.set(e.id, e);
      if (!headMap.has(e.hn)) headMap.set(e.hn, []);
      headMap.get(e.hn).push(e);
      for (const [form, tag] of e.forms) {
        const k = fold(form);
        if (!formMap.has(k)) formMap.set(k, []);
        formMap.get(k).push([e, form, tag]);
      }
      for (const [lang, texts] of Object.entries(e.t)) {
        if (!LANGSET.has(lang)) continue;
        for (const text of texts) {
          const k = fold(text);
          if (!transMap[lang].has(k)) transMap[lang].set(k, []);
          transMap[lang].get(k).push([e, text]);
        }
      }
    }

    const headKeys = [...headMap.keys()].sort();
    const formKeys = [...formMap.keys()].sort();
    for (const l of LANGS) transKeys[l] = [...transMap[l].keys()].sort();

    DB = { meta: payload.meta, entries, byId, headMap, headKeys, formMap, formKeys,
           transMap, transKeys };
    return DB;
  }

  function lowerBound(keys, prefix) {
    let lo = 0, hi = keys.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (keys[mid] < prefix) lo = mid + 1; else hi = mid;
    }
    return lo;
  }
  function prefixKeys(keys, prefix, limit = 300) {
    const out = [];
    for (let i = lowerBound(keys, prefix); i < keys.length && out.length < limit; i++) {
      if (!keys[i].startsWith(prefix)) break;
      out.push(keys[i]);
    }
    return out;
  }

  function add(best, e, score, kind, text = "", tag = "", lang = "") {
    const old = best.get(e.id);
    if (!old || score > old.score)
      best.set(e.id, { entry: e, score, match: { kind, text, tag, lang,
        tag_label: tag ? tagLabel(tag) : "" } });
  }

  function search(query, lang = "ru", limit = 40) {
    const q = fold(query);
    if (!q || !DB) return [];
    if (!LANGSET.has(lang)) lang = "ru";
    const best = new Map();

    const headHits = DB.headMap.get(q) || [];
    for (const e of headHits) add(best, e, 1000, "headword", e.h);
    const pronHead = headHits.some(e => e.pos === "pron");
    for (const [e, form, tag] of DB.formMap.get(q) || []) {
      if (pronHead && e.pos === "pron") continue;
      add(best, e, 900, "form", form, tag);
    }
    for (const [e, text] of DB.transMap[lang].get(q) || [])
      add(best, e, 800, "translation", text, "", lang);

    for (const key of prefixKeys(DB.headKeys, q)) {
      for (const e of DB.headMap.get(key) || [])
        add(best, e, 700, "headword", e.h);
    }
    for (const key of prefixKeys(DB.transKeys[lang], q)) {
      for (const [e, text] of DB.transMap[lang].get(key) || [])
        add(best, e, 550, "translation", text, "", lang);
    }
    if (q.length >= 2) {
      for (const key of prefixKeys(DB.formKeys, q, 500)) {
        for (const [e, form, tag] of DB.formMap.get(key) || [])
          add(best, e, 400, "form", form, tag);
      }
    }
    if (q.length >= 2) {
      for (const e of DB.entries) {
        if (e.hn !== q && e.hn.includes(q)) add(best, e, 300, "headword", e.h);
      }
      for (const [key, hits] of DB.transMap[lang]) {
        if (key !== q && key.includes(q))
          for (const [e, text] of hits) add(best, e, 250, "translation", text, "", lang);
      }
    }
    if (q.length >= 3) {
      for (const key of DB.formKeys) {
        if (key === q || !key.includes(q)) continue;
        for (const [e, form, tag] of DB.formMap.get(key) || [])
          add(best, e, 150, "form", form, tag);
      }
    }
    if (!best.size) {
      for (const l of LANGS) {
        if (l === lang) continue;
        for (const [e, text] of DB.transMap[l].get(q) || [])
          add(best, e, 350, "translation", text, "", l);
        for (const key of prefixKeys(DB.transKeys[l], q, 50)) {
          for (const [e, text] of DB.transMap[l].get(key) || [])
            add(best, e, 200, "translation", text, "", l);
        }
      }
    }

    return [...best.values()]
      .sort((a, b) => b.score - a.score || a.entry.h.localeCompare(b.entry.h, "prs"))
      .slice(0, limit);
  }

  function suggest(prefix, lang = "ru", limit = 10) {
    const q = fold(prefix);
    if (!q || !DB) return [];
    const out = [], seen = new Set();
    if (!LANGSET.has(lang)) lang = "ru";
    for (const key of prefixKeys(DB.headKeys, q, limit * 3)) {
      for (const e of DB.headMap.get(key) || []) {
        if (seen.has(e.id)) continue;
        seen.add(e.id);
        out.push({ word: e.h, translation: (e.t[lang] || []).slice(0, 2).join(" · "), pos: e.pos });
      }
    }
    if (out.length < limit) {
      for (const l of [lang, ...LANGS.filter(x => x !== lang)]) {
        for (const key of prefixKeys(DB.transKeys[l], q, limit * 3)) {
          for (const [e] of DB.transMap[l].get(key) || []) {
            if (seen.has(e.id)) continue;
            seen.add(e.id);
            out.push({ word: e.h, translation: (e.t[lang] || []).slice(0, 2).join(" · "), pos: e.pos });
            if (out.length >= limit) break;
          }
          if (out.length >= limit) break;
        }
        if (out.length >= limit) break;
      }
    }
    return out.slice(0, limit);
  }

  function cellFn(e) {
    const cells = new Map();
    for (const [form, tag] of e.forms) {
      if (!cells.has(tag)) cells.set(tag, []);
      const arr = cells.get(tag);
      if (!arr.includes(form)) arr.push(form);
    }
    return tag => (cells.get(tag) || []).join(" / ");
  }

  function paradigm(e) {
    const cell = cellFn(e);
    const has = tag => (cell(tag) || "").length > 0;

    if (e.pos === "noun" && e.forms.length) {
      return { type: "noun", tables: [{
        title: null, kind: "cases", corner: "Падеж",
        columns: ["Единственное", "Множественное"],
        rows: ["nom", "gen", "dat", "acc"].map(c => ({
          label: CASE_RU[c], cells: [cell(`sg.${c}`), cell(`pl.${c}`)],
        })),
      }] };
    }

    if (["adj", "num", "pron"].includes(e.pos) && e.forms.length) {
      const tables = [];
      for (const g of GENDERS) {
        if (!e.forms.some(([, t]) => t.startsWith(g + "."))) continue;
        tables.push({
          title: GENDER_RU[g], kind: "cases", corner: "Падеж",
          columns: ["Единственное", "Множественное"],
          rows: ["nom", "gen", "dat", "acc"].map(c => ({
            label: CASE_RU[c], cells: [cell(`${g}.sg.${c}`), cell(`${g}.pl.${c}`)],
          })),
        });
      }
      for (const [prefix, title] of [["comp", "Сравнительная степень"], ["superl", "Превосходная степень"]]) {
        const rows = [];
        for (const g of GENDERS) {
          for (const [n, nlabel] of [["sg", "ед."], ["pl", "мн."]]) {
            for (const c of ["nom", "gen", "dat", "acc"]) {
              const v = cell(`${prefix}.${g}.${n}.${c}`);
              if (v) rows.push({ label: `${GENDER_RU[g]} ${nlabel} ${CASE_RU[c].toLowerCase()}`, cells: [v] });
            }
          }
        }
        if (rows.length) tables.push({ title, kind: "list", columns: ["Форма"], rows });
      }
      if (has("adv")) {
        tables.push({ title: "Наречие", kind: "list", columns: ["Форма"], rows: [
          { label: "положительная", cells: [cell("adv")] },
          { label: "сравнительная", cells: [cell("adv.comp")] },
          { label: "превосходная", cells: [cell("adv.superl")] },
        ] });
      }
      return { type: "adj", tables };
    }

    if (e.pos === "verb" && e.forms.length) {
      const finite = [];
      for (const [pronoun, pn] of VERB_ROWS) {
        const vals = ["prs", "prt", "subj"].map(t => cell(`${t}.${pn}`));
        if (vals.some(Boolean)) finite.push({ label: pronoun, cells: vals });
      }
      const tables = [{ title: "Личные формы", kind: "verb", corner: "Лицо",
        columns: ["Настоящее", "Прошедшее", "Сослагательное"], rows: finite }];

      const extra = [];
      if (has("imprt.sg2") || has("imprt.pl2"))
        extra.push({ label: "Повелительное наклонение", cells: [cell("imprt.sg2"), cell("imprt.pl2")] });
      if (has("opt")) extra.push({ label: "Оптатив", cells: [cell("opt")] });
      if (extra.length)
        tables.push({ title: "Наклонения", kind: "verb", corner: "", columns: ["Ед. ч. (ты)", "Мн. ч. (вы)"], rows: extra });

      for (const [title, idx] of [["Перфект (сложное прошедшее)", 1], ["Футурум (сложное будущее)", 2]]) {
        const rows = [];
        for (const row of PERIPHRASTIC) {
          const part = row[3].map(gn => cell(`part.prf.act.${gn}.nom`)).filter(Boolean).join(" / ");
          if (part) rows.push({ label: row[0], cells: [`${row[idx]} ${part}`] });
        }
        if (rows.length) tables.push({ title, kind: "list", columns: ["Форма"], rows });
      }

      for (const [prefix, title] of Object.entries(PART_RU)) {
        if (!e.forms.some(([, t]) => t.startsWith(prefix + "."))) continue;
        for (const g of GENDERS) {
          if (!e.forms.some(([, t]) => t.startsWith(`${prefix}.${g}.`))) continue;
          tables.push({
            title: `${title} — ${GENDER_RU[g]}`, kind: "cases", corner: "Падеж",
            columns: ["Единственное", "Множественное"],
            rows: ["nom", "gen", "dat", "acc"].map(c => ({
              label: CASE_RU[c],
              cells: [cell(`${prefix}.${g}.sg.${c}`), cell(`${prefix}.${g}.pl.${c}`)],
            })),
          });
        }
      }
      return { type: "verb", tables };
    }

    const tags = [...new Set(e.forms.map(([, t]) => t))];
    return { type: "other", tables: tags.length ? [{
      title: null, kind: "list", columns: ["Форма"],
      rows: tags.map(t => ({ label: tagLabel(t), cells: [cell(t)] })),
    }] : [] };
  }

  function entryPayload(e, match = null, withTables = true) {
    const payload = {
      id: e.id, headword: e.h, pos: e.pos, pos_ru: POS_RU[e.pos] || e.pos || "—",
      gender: e.gender, gender_ru: GENDER_RU[e.gender] || "",
      paradigm: e.par, translations: e.t, desc: e.desc, derived_from: e.derived,
      forms_count: e.forms.length,
      match: match ? {
        kind: match.kind, text: match.text, tag: match.tag,
        tag_label: match.tag_label || "", lang: match.lang,
      } : null,
    };
    if (withTables) payload.paradigm_tables = paradigm(e);
    return payload;
  }

  function glossToken(word, dst, limit = 3) {
    const q = fold(word);
    const hits = [], seen = new Set();
    const push = (e, kind, tag) => {
      if (seen.has(e.id)) return;
      seen.add(e.id);
      hits.push([e, kind, tag]);
    };
    const headHits = DB.headMap.get(q) || [];
    for (const e of headHits) push(e, "headword", "");
    const pronHead = headHits.some(e => e.pos === "pron");
    for (const [e, , tag] of DB.formMap.get(q) || []) {
      if (pronHead && e.pos === "pron") continue;
      push(e, "form", tag);
    }
    if (!hits.length) {
      for (const key of prefixKeys(DB.headKeys, q, limit)) {
        for (const e of DB.headMap.get(key) || []) push(e, "headword", "");
      }
    }
    return hits.slice(0, limit).map(([e, kind, tag]) => ({
      headword: e.h, id: e.id, kind, tag, tag_label: tagLabel(tag),
      pos: e.pos, pos_ru: POS_RU[e.pos] || e.pos || "", gender: e.gender,
      translation: e.t[dst] || [], all_translations: e.t,
    }));
  }

  const TOKEN_RE = /\p{L}+(?:['’\-]\p{L}+)*/gu;

  function gloss(text, src = "prs", dst = "ru") {
    if (src !== "prs" && !LANGSET.has(src)) src = "prs";
    if (src === "prs") {
      if (!LANGSET.has(dst)) dst = "ru";
    } else {
      dst = "prs";
    }
    const out = [];
    let pos = 0;
    if (src === "prs") {
      for (const m of text.matchAll(TOKEN_RE)) {
        if (m.index > pos) out.push({ token: text.slice(pos, m.index), space: true });
        const t = m[0];
        out.push({ token: t, space: false, analysis: glossToken(t, dst) });
        pos = m.index + t.length;
      }
    } else {
      for (const m of text.matchAll(TOKEN_RE)) {
        if (m.index > pos) out.push({ token: text.slice(pos, m.index), space: true });
        const t = m[0], q = fold(t);
        const results = (DB.transMap[src].get(q) || []).slice(0, 8).map(([e, matched]) => ({
          headword: e.h, pos: e.pos, pos_ru: POS_RU[e.pos] || e.pos || "", gender: e.gender,
          matched, translations: e.t, id: e.id,
        }));
        out.push({ token: t, space: false, analysis: results });
        pos = m.index + t.length;
      }
    }
    if (pos < text.length) out.push({ token: text.slice(pos), space: true });
    return { text, src, dst, tokens: out };
  }

  // ── fetch interception ───────────────────────────────────────────────────
  function jsonResponse(obj, status = 200) {
    return new Response(JSON.stringify(obj), {
      status, headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }

  window.fetch = async (input, init) => {
    const raw = typeof input === "string" ? input : (input && input.url) || "";
    let u;
    try { u = new URL(raw, location.href); } catch { return realFetch(input, init); }
    if (!u.pathname.includes("/api/")) return realFetch(input, init);
    await ready;
    const q = u.searchParams;
    try {
      if (u.pathname.endsWith("/api/search")) {
        const query = (q.get("q") || "").trim();
        const lang = q.get("lang") || "ru";
        const limit = Math.min(parseInt(q.get("limit") || "40", 10) || 40, 100);
        return jsonResponse({ query, lang,
          results: search(query, lang, limit).map(r => entryPayload(r.entry, r.match, false)) });
      }
      if (u.pathname.endsWith("/api/suggest")) {
        return jsonResponse({ suggestions: suggest(q.get("q") || "", q.get("lang") || "ru") });
      }
      if (u.pathname.endsWith("/api/entry")) {
        const e = DB.byId.get(parseInt(q.get("id") || "", 10));
        return e ? jsonResponse(entryPayload(e, null, true)) : jsonResponse({ error: "not found" }, 404);
      }
      if (u.pathname.endsWith("/api/gloss")) {
        return jsonResponse(gloss(q.get("text") || "", q.get("src") || "prs", q.get("dst") || "ru"));
      }
      if (u.pathname.endsWith("/api/random")) {
        const e = DB.entries[Math.floor(Math.random() * DB.entries.length)];
        return jsonResponse(entryPayload(e, null, true));
      }
      if (u.pathname.endsWith("/api/stats")) return jsonResponse(DB.meta);
      return jsonResponse({ error: "not found" }, 404);
    } catch (err) {
      return jsonResponse({ error: String(err) }, 500);
    }
  };
})();
