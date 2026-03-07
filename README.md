# newsletter

Deterministic weekly newsletter tooling for CFC.

## Why

Reduce probabilistic newsletter generation by splitting the workflow into strict stages:

1. collect data
2. create deterministic draft structure
3. render HTML from template
4. run quality checks
5. package artifact
6. build static artifact site (`docs/`) for browser viewing (GitHub Pages)

## Install

```bash
cd ~/repos/newsletter
python3 -m venv .venv
./.venv/bin/python -m pip install -e .
```

## v1 workflow

```bash
./.venv/bin/newsletter data collect \
  --start-date 2026-03-01 \
  --end-date 2026-03-07 \
  --out build/collected.json \
  --overrides overrides/week-2026-W10.yaml

./.venv/bin/newsletter content draft \
  --input build/collected.json \
  --out build/draft.json

./.venv/bin/newsletter render html \
  --input build/draft.json \
  --out build/newsletter.html

./.venv/bin/newsletter check html --input build/newsletter.html

./.venv/bin/newsletter render package \
  --draft build/draft.json \
  --html build/newsletter.html \
  --out artifacts/newsletter-2026-W10.json

./.venv/bin/newsletter site build --artifacts-dir artifacts --docs-dir docs
```

Open `docs/index.html` or publish via GitHub Pages.

## Data source (v1)

`data collect` currently sources from:
- `mcp-localline orders-export --start-date --end-date`
- optional weekly override YAML

It computes deterministic summaries (orders count, line-item count, top products/vendors) and writes normalized collected JSON.
