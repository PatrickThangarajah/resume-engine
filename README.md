# Resume engine

**The idea in one line:** the AI is a jukebox, not a band. It picks which of your tracks play and in what order; it never sings. Your recorded tracks (your exact bullets) sound the same every time.

```
 job description ──► AI ──► recipe.yaml (IDs only)
                                   │
            bank.yaml (your words) ┼──► build.py ──► checks ──► PDF + Overleaf zip
            latex/layout.tex (spacing)
```

## Files

| File | What it is | Who edits it |
|---|---|---|
| `bank.yaml` | Every headline, summary, skill row, bullet and publication you have written, each with an ID | You only |
| `latex/layout.tex` | Every spacing number, in one place | You, rarely |
| `recipes/*.yaml` | One per application: which IDs, in what order | The AI (or you) |
| `AI_PROMPT.md` | The instructions to paste into Claude or ChatGPT | Nobody |
| `catalog.md` | Plain-text menu of your bank for the AI (regenerate after editing the bank) | Generated |
| `build.py` | Validates, builds, compiles, fits to page | Nobody |

## Tailoring for a new job

1. Paste `AI_PROMPT.md`, then `catalog.md`, then the job description into the AI.
2. Save its YAML as `recipes/<company_role>.yaml`.
3. `python build.py build recipes/<company_role>.yaml`
4. Open `out/Patrick_Thangarajah_Resume_<name>.pdf`. To edit in Overleaf, upload `out/<name>_overleaf.zip` as a new project (main file: `main.tex`, compiler: XeLaTeX).

Or skip steps 1 to 3: send Claude the job description and this folder, and ask for the PDF.

### On GitHub (no install, works from your phone)

1. Add a recipe file in `recipes/` (GitHub app or website: Add file, Create new file, paste the AI's YAML, Commit).
2. About 2 minutes later the PDF appears in `pdf/`. The Overleaf zip appears in `pdf/overleaf/`.
3. Editing `bank.yaml` or `latex/layout.tex` rebuilds every recipe. Actions tab, "Build resumes", "Run workflow" also rebuilds everything.
4. If a recipe is rejected, the Actions run turns red and its summary says exactly why.
5. The build report also lists any bullet that runs 3 or more lines, so you can write a shorter version in `bank.yaml` under the same group.

## What the builder refuses

- An ID that is not in the bank (with a "did you mean" hint), so typos and invented content cannot get in
- Two wordings of the same achievement on one resume (bullets sharing a `group`)
- A skill that is not already somewhere in your bank
- Any rewritten sentence in place of an ID
- Unescaped `& % # $ _`, stray `\newline` / `\vspace`, odd dash characters, unbalanced braces in the bank

## How it fits the page

1. Builds at normal spacing.
2. If too long, shrinks **every** gap by the same factor (1.0, then 0.6, then 0.3), so spacing stays even.
3. If still too long, removes items in the order listed under `if_space` in the recipe, and reports what it removed.

## Editing your wording

Change the text in `bank.yaml`, then run `python build.py check` and `python build.py catalog > catalog.md`. Every future resume picks up the change. To add a new phrasing of an existing achievement, add a bullet with the same `group` as the original.

## Requirements

Python 3 with PyYAML, and a TeX install with XeLaTeX (TeX Live or MacTeX). Overleaf alone also works through the zip.
