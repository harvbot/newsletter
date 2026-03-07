# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e .
```

## Full Weekly Pipeline

```bash
# 1. Collect data from orders export (via mcp-localline) + storefront API + optional overrides
./.venv/bin/newsletter data collect \
  --start-date 2026-03-01 \
  --end-date 2026-03-07 \
  --collect-dir build/data-collect \
  --storefront-url "https://cfc.localline.ca/storefront/api/products" \
  --storefront-category new \
  --out build/collected.json \
  --overrides overrides/week-2026-W10.yaml

# 2. Generate deterministic draft JSON from collected data
./.venv/bin/newsletter content draft --input build/collected.json --out build/draft.json

# 3. Render HTML from Jinja2 template
./.venv/bin/newsletter render html --input build/draft.json --out build/newsletter.html

# 4. Quality gate (checks for required HTML sections)
./.venv/bin/newsletter check html --input build/newsletter.html

# 5. Package artifact (bundles draft JSON + HTML into a single artifact JSON)
./.venv/bin/newsletter render package --draft build/draft.json --html build/newsletter.html --out artifacts/newsletter-2026-W10.json

# 6. Build static site (docs/ directory for GitHub Pages)
./.venv/bin/newsletter site build --artifacts-dir artifacts --docs-dir docs
```

## Architecture

The pipeline is a linear sequence of CLI stages, each reading/writing JSON files. No database, no server.

### Stage flow

```
data collect → build/collected.json
                  ↓
content draft → build/draft.json
                  ↓
render html   → build/newsletter.html
                  ↓
check html    (quality gate, exits 1 on failure)
                  ↓
render package → artifacts/newsletter-YYYY-WNN.json  (draft JSON + rendered HTML bundled)
                  ↓
site build    → docs/index.html + docs/*.html + docs/*.json  (GitHub Pages)
```

### Key schemas

- **`CollectedInput`** (`schema_version: "1.2"`) — output of `data collect`. Contains `metrics`, `top_products`, `top_vendors`, `storefront_new_products`, `price_list_products`, `overrides`, and `source_files` pointers.
- **`Draft`** (`schema_version: "1.0"`) — output of `content draft`. Contains `week_label`, `subject`, `preheader`, and `sections` (list of `{title, items}`).
- **Artifact** — final packaged JSON with `draft` + `html` fields, written to `artifacts/` and copied to `docs/` by `site build`.

### Data collection fan-out (`src/newsletter/data_collect/pipeline.py`)

`run_collection()` writes per-source files into `build/data-collect/<start>_<end>/`:
- `orders-export.raw.json` + `orders-export.normalized.json` — from `mcp-localline orders-export` CLI subprocess
- `storefront.raw.json` + `storefront.normalized.json` — from Local Line storefront HTTP API (public, category filter)
- `price-list.raw.json` + `price-list.normalized.json` — from `mcp-localline storefront-price-list` (anonymous auth → default price list → products); skip with `--no-price-list`
- `overrides.json` — from optional YAML file

### Overrides YAML (`overrides/`)

Optional per-week YAML file with two keys:
- `notes` — list of strings used as the "This Week at CFC" section items
- `featured_products` — list of product name strings overriding storefront defaults

### Template (`templates/cfc-weekly.html.j2`)

Jinja2 template rendered with `draft` dict. Used by `render html` stage.

### Deployment

GitHub Actions (`pages.yml`) deploys `docs/` to GitHub Pages on every push to `main`. Run `site build` locally and commit `docs/` to update the live site.

### Artifacts convention

Artifact files are named `newsletter-YYYY-WNN.json` (e.g. `newsletter-2026-W10.json`) and committed to `artifacts/`. The `site build` command re-indexes all `artifacts/*.json` into `docs/`.
