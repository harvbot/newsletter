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
  --collect-dir build/data-collect \
  --storefront-url "https://cfc.localline.ca/storefront/api/products" \
  --storefront-category new \
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

## Data source fan-out (v1)

`data collect` now runs a fan-out pipeline into its own directory (`build/data-collect/<start>_<end>/`):

1. `orders-export` via `mcp-localline`
   - writes `orders-export.raw.json` + `orders-export.normalized.json`
2. Local Line storefront products API (category=`new`)
   - writes `storefront.raw.json` + `storefront.normalized.json`
   - normalized fields include product name, image URL, and price cents
3. Default storefront price list via `mcp-localline storefront-price-list`
   - fetches anonymous storefront token → `GET /api/storefront/v2/price-lists/default/` → products
   - writes `price-list.raw.json` + `price-list.normalized.json`
   - normalized fields include product name, sku, price cents, image URL, and vendor name
   - skip with `--no-price-list`
4. Optional weekly override YAML
   - writes `overrides.json`

Then it composes `build/collected.json` (schema v1.2) with source provenance and pointers to source files.

## Override rhythm (recommended)

Keep one weekly file and update it throughout the week:
- `overrides/week-YYYY-WW.yaml`

Fields currently supported:
- `hero_products`: pin/force products into the NEW THIS WEEK flyer cards (optional `link` per tile)
- `storefront_link`: default URL used when product tiles are clicked
- `notes`: quick notes section bullets
- `main_message`: narrative/storytelling lines for the newsletter's core seasonal message
- `suppress_products`: hide products by name
