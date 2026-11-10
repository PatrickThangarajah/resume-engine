#!/usr/bin/env python3
"""
Resume engine: recipe (IDs only) + bank (your words) -> LaTeX -> PDF.

  python build.py check                      lint bank.yaml
  python build.py catalog                    print the ID menu to paste into an AI
  python build.py build recipes/x.yaml       validate, render, compile, fit to page
  python build.py build recipes/*.yaml       several at once

The AI never writes text. It only returns a recipe. This script copies your
exact wording from bank.yaml into one fixed layout, so wording and spacing
cannot drift between versions.
"""
import difflib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Needs PyYAML:  pip install pyyaml")

ROOT = Path(__file__).resolve().parent
BANK = ROOT / "bank.yaml"
LATEX = ROOT / "latex"
OUT = ROOT / "out"
NAME_ON_FILE = "Patrick_Thangarajah_Resume"

SECTIONS = ["summary", "skills", "education", "experience", "projects", "publications"]
RECIPE_KEYS = {"name", "target", "pages", "headline", "contact", "summary", "sections",
               "titles", "skills", "education", "experience", "projects",
               "publications", "if_space"}


class RecipeError(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def clean(text):
    """Collapse the YAML line folding into single spaces. Never changes words."""
    return re.sub(r"\s+", " ", str(text)).strip()


def suggest(bad, options):
    near = difflib.get_close_matches(str(bad), list(options), n=3, cutoff=0.5)
    return f" (did you mean: {', '.join(near)}?)" if near else ""


def need(cond, msg):
    if not cond:
        raise RecipeError(msg)


def groups_of(bullet):
    g = bullet.get("group")
    if g is None:
        return []
    return g if isinstance(g, list) else [g]


# --------------------------------------------------------------------------- lint
UNESCAPED = re.compile(r"(?<!\\)[&%#$_]")
BANNED = {
    "\\newline": "line breaks belong to the layout, not the text",
    "\\vspace": "spacing belongs in latex/layout.tex",
    "\\\\": "manual line break",
    "\u2011": "non-breaking hyphen (use a normal -)",
    "\u2013": "en dash character (type -- instead)",
    "\u2014": "em dash",
}


def lint_text(where, text, problems):
    t = clean(text)
    for m in UNESCAPED.finditer(t):
        problems.append(f"{where}: unescaped '{m.group()}' (write \\{m.group()})")
    for k, why in BANNED.items():
        if k in t:
            problems.append(f"{where}: contains {k!r}: {why}")
    if t.count("{") != t.count("}"):
        problems.append(f"{where}: unbalanced braces")


def iter_bank_text(bank):
    for k, v in bank["contact"].items():
        yield f"contact.{k}", v
    for k, v in bank["headlines"].items():
        yield f"headlines.{k}", v
    for k, v in bank["summaries"].items():
        yield f"summaries.{k}", v
    for k, row in bank["skills"]["rows"].items():
        yield f"skills.{k}.category", row["category"]
        for it in row["items"]:
            yield f"skills.{k}.items", it
    for k, e in bank["education"].items():
        for f in ("institution", "degree", "location", "dates"):
            yield f"education.{k}.{f}", e[f]
        for n, t in e.get("notes", {}).items():
            yield f"education.{k}.notes.{n}", t
    for sec in ("experience", "projects"):
        for k, e in bank[sec].items():
            for f in ("org", "location", "dates", "role" if sec == "experience" else "title"):
                yield f"{sec}.{k}.{f}", e[f]
            for b, bl in e["bullets"].items():
                yield f"{sec}.{k}.{b}", bl["text"]
    for k, v in bank["publications"].items():
        yield f"publications.{k}", v


def lint_bank(bank):
    problems = []
    for where, text in iter_bank_text(bank):
        lint_text(where, text, problems)
    for sec in ("experience", "projects"):
        for k, e in bank[sec].items():
            if not re.fullmatch(r"[A-Z][a-z]{2} \d{4} - ([A-Z][a-z]{2} \d{4}|Present)", e["dates"]):
                problems.append(f"{sec}.{k}.dates: use the form 'Sep 2023 - Jul 2026'")
    return problems


# --------------------------------------------------------------------------- catalog
def plain(t):
    t = clean(t).replace("\\&", "&").replace("\\%", "%").replace("--", "-")
    return re.sub(r"\\(textbf|emph)\{([^}]*)\}", r"\2", t)


def catalog(bank):
    L = ["# RESUME BANK CATALOG (pick IDs only; never write or edit text)", ""]
    L.append("## contact: " + ", ".join(f"{k} = {v}" for k, v in bank["contact"].items()))
    L.append("\n## headlines")
    L += [f"- {k}: {plain(v)}" for k, v in bank["headlines"].items()]
    L.append("\n## summaries")
    L += [f"- {k}: {plain(v)}" for k, v in bank["summaries"].items()]
    L.append("\n## skills rows (category: items)")
    for k, r in bank["skills"]["rows"].items():
        L.append(f"- {k}: [{plain(r['category'])}] {', '.join(plain(i) for i in r['items'])}")
    L.append("- extra_vocabulary (may be added to a row): "
             + ", ".join(plain(i) for i in bank["skills"]["extra_vocabulary"]))
    L.append("\n## education (entry: notes)")
    for k, e in bank["education"].items():
        L.append(f"- {k}: {plain(e['degree'])}, {e['institution']}")
        L += [f"    - {n}: {plain(t)}" for n, t in e["notes"].items()]
    for sec in ("experience", "projects"):
        L.append(f"\n## {sec} (entry: bullets; bullets sharing a [group] are alternatives, pick at most one)")
        for k, e in bank[sec].items():
            head = e.get("role") or e.get("title")
            L.append(f"- {k}: {plain(head)} | {plain(e['org'])} | {e['dates']}")
            for b, bl in e["bullets"].items():
                g = groups_of(bl)
                tag = f" [{'+'.join(g)}]" if g else ""
                L.append(f"    - {b}{tag}: {plain(bl['text'])}")
    L.append("\n## publications")
    L += [f"- {k}: {plain(v)[:140]}..." for k, v in bank["publications"].items()]
    L.append("\n## section titles allowed")
    L += [f"- {k}: {v}" for k, v in bank["section_titles"].items()]
    return "\n".join(L)


# --------------------------------------------------------------------------- recipe
def normalise_pairs(lst, what):
    """[- id: [a, b]] or [- id] -> [(id, [a, b] or None)]"""
    out = []
    for item in lst or []:
        if isinstance(item, str):
            out.append((item, None))
        elif isinstance(item, dict) and len(item) == 1:
            (k, v), = item.items()
            need(v is None or isinstance(v, list), f"{what}.{k}: expected a list of IDs")
            out.append((k, v))
        else:
            raise RecipeError(f"{what}: each item must be 'id' or 'id: [bullet ids]', got {item!r}")
    return out


def validate(recipe, bank):
    unknown = set(recipe) - RECIPE_KEYS
    need(not unknown, f"unknown recipe keys: {sorted(unknown)} (allowed: {sorted(RECIPE_KEYS)})")
    need(recipe.get("name") and re.fullmatch(r"[A-Za-z0-9_\-]+", recipe["name"]),
         "name: required, letters/numbers/_/- only")
    need(recipe.get("pages", 1) in (1, 2), "pages: must be 1 or 2")

    need(recipe.get("headline") in bank["headlines"],
         f"headline: {recipe.get('headline')!r} not in bank{suggest(recipe.get('headline'), bank['headlines'])}")
    for c in recipe.get("contact", []):
        need(c in bank["contact"], f"contact: {c!r} not in bank{suggest(c, bank['contact'])}")

    sections = recipe.get("sections", SECTIONS)
    for s in sections:
        need(s in SECTIONS, f"sections: {s!r} is not a section{suggest(s, SECTIONS)}")
        need(recipe.get(s) is not None, f"sections lists {s!r} but recipe has no {s}:")
    need(len(set(sections)) == len(sections), "sections: duplicated")
    for s, t in (recipe.get("titles") or {}).items():
        need(t in bank["section_titles"].get(s, []),
             f"titles.{s}: {t!r} not allowed (choose from {bank['section_titles'].get(s)})")

    if "summary" in sections:
        need(recipe["summary"] in bank["summaries"],
             f"summary: {recipe['summary']!r} not in bank{suggest(recipe['summary'], bank['summaries'])}")

    if "skills" in sections:
        rows = bank["skills"]["rows"]
        vocab = {i for r in rows.values() for i in r["items"]} | set(bank["skills"]["extra_vocabulary"])
        seen = set()
        for item in recipe["skills"]:
            rid, mods = (item, {}) if isinstance(item, str) else next(iter(item.items()))
            mods = mods or {}
            need(rid in rows, f"skills: row {rid!r} not in bank{suggest(rid, rows)}")
            need(rid not in seen, f"skills: row {rid!r} used twice")
            seen.add(rid)
            need(set(mods) <= {"remove", "add"}, f"skills.{rid}: only 'remove' and 'add' allowed")
            for r in mods.get("remove", []):
                need(r in rows[rid]["items"], f"skills.{rid}.remove: {r!r} is not in that row")
            for a in mods.get("add", []):
                need(a in vocab, f"skills.{rid}.add: {a!r} is not a skill in your bank. "
                                 f"Add it to skills.extra_vocabulary first if it is real.")

    def check_entries(sec, need_bullets):
        pairs = normalise_pairs(recipe[sec], sec)
        need(len({k for k, _ in pairs}) == len(pairs), f"{sec}: an entry is listed twice")
        for k, bl in pairs:
            entry = bank[sec].get(k)
            need(entry is not None, f"{sec}: {k!r} not in bank{suggest(k, bank[sec])}")
            pool = entry["notes"] if sec == "education" else entry["bullets"]
            if need_bullets:
                need(bl, f"{sec}.{k}: pick at least one bullet")
            used_groups = {}
            for b in bl or []:
                need(b in pool, f"{sec}.{k}: bullet {b!r} not in bank{suggest(b, pool)}")
                need((bl or []).count(b) == 1, f"{sec}.{k}: bullet {b!r} listed twice")
                if sec != "education":
                    for g in groups_of(pool[b]):
                        need(g not in used_groups,
                             f"{sec}.{k}: {b!r} and {used_groups.get(g)!r} say the same thing "
                             f"(group '{g}'); keep one")
                        used_groups[g] = b
        return pairs

    for sec in ("education", "experience", "projects"):
        if sec in sections:
            check_entries(sec, need_bullets=(sec != "education"))

    if "publications" in sections:
        for p in recipe["publications"]:
            need(p in bank["publications"], f"publications: {p!r} not in bank{suggest(p, bank['publications'])}")

    for path in recipe.get("if_space", []) or []:
        parts = path.split(".")
        need(parts[0] in ("experience", "projects", "education", "publications", "skills") and len(parts) in (2, 3),
             f"if_space: {path!r} must look like section.entry or section.entry.bullet")


# --------------------------------------------------------------------------- render
GAP_SCALES = [1.0, 0.6, 0.3]


def render(recipe, bank, gap=1.0):
    T = []
    w = T.append
    w("% Resume of Patrick Thangarajah")
    w("\\documentclass[10pt, letterpaper]{awesome-cv}")
    w("\\fontdir[fonts/]")
    w(f"\\newcommand{{\\GapScale}}{{{gap}}}")
    w("\\input{layout.tex}")
    w("\\name{}{Patrick Thangarajah}")
    w(f"\\position{{{clean(bank['headlines'][recipe['headline']])}}}")
    contact = recipe.get("contact") or ["phone", "email_gmail", "linkedin"]
    w("\\extrainfo{" + " | ".join(clean(bank["contact"][c]) for c in contact) + "}")
    w("\\hypersetup{pdftitle={Patrick Thangarajah Resume}, pdfauthor={Patrick Thangarajah}, "
      "pdfsubject={Resume}, pdfcreator={LaTeX}, pdfkeywords={}}")
    w("\\begin{document}")
    w("\\makecvheader")
    titles = {s: bank["section_titles"][s][0] for s in SECTIONS}
    titles.update(recipe.get("titles") or {})

    def entries(sec, pairs, head_key):
        w("\\EntriesStart")
        for i, (k, bl) in enumerate(pairs):
            e = bank[sec][k]
            if i:
                w("\\NextEntry")
            if sec == "education":
                title, sub, pool = e["degree"], e["institution"], e["notes"]
                texts = [pool[b] for b in bl or []]
            else:
                title, sub, pool = e[head_key], e["org"], e["bullets"]
                texts = [pool[b]["text"] for b in bl]
            w(f"\\ResumeEntry{{{clean(title)}}}{{{clean(sub)}}}{{{clean(e['location'])}}}{{{clean(e['dates'])}}}")
            if texts:
                w("\\begin{ResumeBullets}")
                for t in texts:
                    w(f"  \\item \\descriptionstyle{{{clean(t)}}}")
                w("\\end{ResumeBullets}")

    for s in recipe.get("sections", SECTIONS):
        w(f"\n\\cvsection{{{titles[s]}}}")
        if s == "summary":
            w(f"\\ResumeSummary{{{clean(bank['summaries'][recipe['summary']])}}}")
        elif s == "skills":
            rows = bank["skills"]["rows"]
            for item in recipe["skills"]:
                rid = item if isinstance(item, str) else next(iter(item))
                w(f"\\MeasureSkill{{{clean(rows[rid]['category'])}}}")
            w("\\begin{ResumeSkills}")
            for item in recipe["skills"]:
                rid, mods = (item, {}) if isinstance(item, str) else next(iter(item.items()))
                mods = mods or {}
                items = [i for i in rows[rid]["items"] if i not in mods.get("remove", [])]
                items += [a for a in mods.get("add", []) if a not in items]
                w(f"  \\ResumeSkill{{{clean(rows[rid]['category'])}}}{{{', '.join(items)}}}")
            w("\\end{ResumeSkills}")
        elif s == "education":
            entries("education", normalise_pairs(recipe["education"], "education"), None)
        elif s == "experience":
            entries("experience", normalise_pairs(recipe["experience"], "experience"), "role")
        elif s == "projects":
            entries("projects", normalise_pairs(recipe["projects"], "projects"), "title")
        elif s == "publications":
            w("\\begin{ResumePubs}")
            for p in recipe["publications"]:
                w(f"  \\item {clean(bank['publications'][p])}")
            w("\\end{ResumePubs}")
    w("\n\\end{document}")
    return "\n".join(T) + "\n"


# --------------------------------------------------------------------------- compile
def compile_tex(workdir):
    env = dict(os.environ)
    have = subprocess.run(["kpsewhich", "sourcesanspro.sty"], capture_output=True, text=True).stdout.strip()
    if not have:  # Overleaf has it; some local installs do not
        env["TEXINPUTS"] = str(LATEX / "shim") + "//:" + env.get("TEXINPUTS", "")
    r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                       cwd=workdir, env=env, capture_output=True, text=True, timeout=300)
    log = (workdir / "main.log").read_text(encoding="utf-8", errors="replace")
    errors = [l for l in log.splitlines() if l.startswith("!")]
    if r.returncode != 0 or errors:
        raise RecipeError("LaTeX failed:\n  " + "\n  ".join(errors[:5] or ["see out/.../main.log"]))
    m = re.search(r"Output written on main\.pdf \((\d+) page", log)
    overfull = sum(float(x) > 2.0 for x in re.findall(r"^Overfull \\hbox \((\d+\.\d+)pt", log, flags=re.M))
    return int(m.group(1)), overfull


def drop(recipe, path):
    parts = path.split(".")
    sec = parts[0]
    if sec == "publications":
        recipe["publications"] = [p for p in recipe["publications"] if p != parts[1]]
        return
    if sec == "skills":
        recipe["skills"] = [s for s in recipe["skills"]
                            if (s if isinstance(s, str) else next(iter(s))) != parts[1]]
        return
    pairs = normalise_pairs(recipe[sec], sec)
    new = []
    for k, bl in pairs:
        if k == parts[1]:
            if len(parts) == 2:
                continue
            bl = [b for b in bl if b != parts[2]]
            if not bl and sec != "education":
                continue
        new.append({k: bl})
    recipe[sec] = new


def build(recipe_path, bank):
    recipe = load_yaml(recipe_path)
    validate(recipe, bank)
    name = recipe["name"]
    work = OUT / name
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(LATEX, work, ignore=shutil.ignore_patterns("shim"))
    target = recipe.get("pages", 1)
    dropped = []
    queue = list(recipe.get("if_space") or [])
    while True:
        for gap in GAP_SCALES:  # first: tighten every gap equally
            (work / "main.tex").write_text(render(recipe, bank, gap), encoding="utf-8")
            pages, overfull = compile_tex(work)
            if pages <= target:
                break
        if pages <= target or not queue:
            break
        step = queue.pop(0)     # then: drop content, in the order the recipe allows
        drop(recipe, step)
        validate(recipe, bank)
        dropped.append(step)
    pdf = OUT / f"{NAME_ON_FILE}_{name}.pdf"
    shutil.copy(work / "main.pdf", pdf)
    z = OUT / f"{name}_overleaf.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in work.rglob("*"):
            if f.is_file() and f.suffix not in (".aux", ".log", ".out", ".pdf"):
                zf.write(f, f.relative_to(work))
    status = "OK" if pages <= target else f"OVER: {pages} pages, target {target}"
    print(f"[{status}] {name}: {pages} page(s), gap scale {gap} -> {pdf.relative_to(ROOT)}")
    if dropped:
        print("   trimmed to fit: " + ", ".join(dropped))
    if overfull:
        print(f"   note: {overfull} line(s) run into the margin; see out/{name}/main.log")
    return pages <= target


# --------------------------------------------------------------------------- main
def main(argv):
    if len(argv) < 2 or argv[1] not in ("check", "catalog", "build"):
        print(__doc__)
        return 1
    bank = load_yaml(BANK)
    problems = lint_bank(bank)
    if argv[1] == "check" or problems:
        if problems:
            print("bank.yaml problems:\n  " + "\n  ".join(problems))
            return 1
        print("bank.yaml OK")
        return 0
    if argv[1] == "catalog":
        print(catalog(bank))
        return 0
    OUT.mkdir(exist_ok=True)
    ok = True
    for p in argv[2:]:
        try:
            ok &= build(p, bank)
        except RecipeError as e:
            print(f"[REJECTED] {p}\n   {e}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
