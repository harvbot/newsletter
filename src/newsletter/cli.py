from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import typer
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field

from .data_collect import run_collection

app = typer.Typer(help="Deterministic newsletter pipeline")
data_app = typer.Typer(help="Data collection")
content_app = typer.Typer(help="Draft generation")
render_app = typer.Typer(help="HTML rendering")
check_app = typer.Typer(help="Quality gates")
site_app = typer.Typer(help="Artifact site generation")

app.add_typer(data_app, name="data")
app.add_typer(content_app, name="content")
app.add_typer(render_app, name="render")
app.add_typer(check_app, name="check")
app.add_typer(site_app, name="site")


class CollectedInput(BaseModel):
    schema_version: str = "1.1"
    generated_at_et: str
    window: dict[str, str]
    provenance: dict[str, Any]
    metrics: dict[str, Any]
    top_products: list[dict[str, Any]] = Field(default_factory=list)
    top_vendors: list[dict[str, Any]] = Field(default_factory=list)
    storefront_new_products: list[dict[str, Any]] = Field(default_factory=list)
    price_list_products: list[dict[str, Any]] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)
    source_files: dict[str, Any] = Field(default_factory=dict)


class Draft(BaseModel):
    schema_version: str = "1.0"
    generated_at_et: str
    week_label: str
    subject: str
    preheader: str
    sections: list[dict[str, Any]]
    provenance: dict[str, Any]


def _now_et() -> str:
    return datetime.now(ZoneInfo("America/Toronto")).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


@data_app.command("collect")
def data_collect(
    start_date: str = typer.Option(..., "--start-date", help="YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end-date", help="YYYY-MM-DD"),
    out: Path = typer.Option(Path("build/collected.json"), "--out"),
    collect_dir: Path = typer.Option(Path("build/data-collect"), "--collect-dir", help="Per-source collection outputs"),
    mcp_command: str = typer.Option("mcp-localline", "--mcp-command"),
    overrides: Path = typer.Option(Path(""), "--overrides", help="Optional YAML overrides file"),
    storefront_url: str = typer.Option("", "--storefront-url", help="Local Line storefront products API endpoint"),
    storefront_category: str = typer.Option("new", "--storefront-category", help="Storefront category filter"),
    storefront_token: str = typer.Option("", "--storefront-token", help="Optional storefront API bearer token"),
    price_list: bool = typer.Option(True, "--price-list/--no-price-list", help="Collect default storefront price list via mcp-localline"),
) -> None:
    try:
        result = run_collection(
            start_date=start_date,
            end_date=end_date,
            out_path=out,
            collect_dir=collect_dir,
            mcp_command=mcp_command,
            overrides_path=overrides,
            storefront_url=storefront_url,
            storefront_category=storefront_category,
            storefront_token=storefront_token,
            collect_price_list=price_list,
            now_et=_now_et(),
        )
    except Exception as e:
        raise typer.BadParameter(str(e))

    typer.echo(json.dumps(result, indent=2))


@content_app.command("draft")
def content_draft(
    input: Path = typer.Option(..., "--input"),
    out: Path = typer.Option(Path("build/draft.json"), "--out"),
) -> None:
    collected = CollectedInput(**_read_json(input))
    week_label = f"{collected.window['start_date']} to {collected.window['end_date']}"

    mcp_new_products = [
        p for p in collected.price_list_products
        if isinstance(p, dict) and str(p.get("price_list_category_name", "")).strip().lower() == "new"
    ]

    featured = collected.overrides.get("featured_products") if isinstance(collected.overrides, dict) else None
    if not isinstance(featured, list) or not featured:
        featured = [p.get("name", "") for p in mcp_new_products[:8] if p.get("name")]
        if not featured:
            featured = [p["name"] for p in collected.top_products[:5]]

    overrides = collected.overrides if isinstance(collected.overrides, dict) else {}
    notes = [str(x) for x in overrides.get("notes", [])] if isinstance(overrides.get("notes"), list) else []
    main_message = [str(x) for x in overrides.get("main_message", [])] if isinstance(overrides.get("main_message"), list) else []
    storefront_link = str(overrides.get("storefront_link") or "https://cfc.localline.ca/storefront").strip()
    suppress = set(str(x).strip().lower() for x in overrides.get("suppress_products", []) if str(x).strip()) if isinstance(overrides.get("suppress_products"), list) else set()

    hero_overrides = overrides.get("hero_products", []) if isinstance(overrides.get("hero_products"), list) else []
    hero_products: list[dict[str, Any]] = []

    # 1) explicit weekly hero pins first
    for hp in hero_overrides:
        if not isinstance(hp, dict):
            continue
        name = str(hp.get("name", "")).strip()
        if not name:
            continue
        hero_products.append(
            {
                "name": name,
                "price": str(hp.get("price") or "Price TBD"),
                "vendor": str(hp.get("vendor") or ""),
                "image": str(hp.get("image") or ""),
                "note": str(hp.get("note") or ""),
                "link": str(hp.get("link") or storefront_link),
            }
        )

    # 2) fallback from MCP products with diversity across vendors + product types
    candidates = [p for p in collected.price_list_products if isinstance(p, dict)]

    def _to_card(p: dict[str, Any]) -> dict[str, Any]:
        price_cents = p.get("price_cents")
        price = f"${(price_cents or 0)/100:.2f}" if isinstance(price_cents, int) else "Price TBD"
        product_type = str(p.get("price_list_category_name") or "").strip()
        return {
            "name": str(p.get("name") or "").strip(),
            "price": price,
            "vendor": str(p.get("vendor_name") or "").strip(),
            "image": str(p.get("image_url") or "").strip(),
            "note": f"Type: {product_type}" if product_type else "",
            "link": storefront_link,
        }

    max_cards = 12
    seen_vendors = {str(x.get("vendor") or "").strip().lower() for x in hero_products if str(x.get("vendor") or "").strip()}
    seen_types = set()
    for x in hero_products:
        note = str(x.get("note") or "")
        if note.lower().startswith("type:"):
            seen_types.add(note.split(":", 1)[1].strip().lower())

    while len(hero_products) < max_cards:
        best_idx = None
        best_score = -1

        for idx, p in enumerate(candidates):
            name = str(p.get("name") or "").strip()
            if not name or name.lower() in suppress:
                continue
            if any(x.get("name", "").lower() == name.lower() for x in hero_products):
                continue

            vendor = str(p.get("vendor_name") or "").strip().lower()
            ptype = str(p.get("price_list_category_name") or "").strip().lower()
            is_new = ptype == "new"

            score = 0
            if vendor and vendor not in seen_vendors:
                score += 3
            if ptype and ptype not in seen_types:
                score += 3
            if is_new:
                score += 2

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is None:
            break

        picked = candidates.pop(best_idx)
        card = _to_card(picked)
        if not card["name"]:
            continue
        hero_products.append(card)

        vendor_norm = card["vendor"].strip().lower()
        if vendor_norm:
            seen_vendors.add(vendor_norm)
        if card["note"].lower().startswith("type:"):
            seen_types.add(card["note"].split(":", 1)[1].strip().lower())

    hero_lines = []
    for p in hero_products:
        line = f"{p['name']} ({p['price']})"
        if p.get("vendor"):
            line += f" — {p['vendor']}"
        if p.get("image"):
            line += f" — {p['image']}"
        if p.get("note"):
            line += f" — {p['note']}"
        if p.get("link"):
            line += f" — {p['link']}"
        hero_lines.append(line)

    sections = [
        {
            "title": "NEW THIS WEEK",
            "items": hero_lines or ["No MCP products were collected in category 'New' for this run."],
        },
        {
            "title": "Quick Notes",
            "items": notes or ["Fresh availability posted weekly. Quantities can change quickly."],
        },
    ]

    if main_message:
        sections.append({"title": "Main Message", "items": main_message})

    draft = Draft(
        generated_at_et=_now_et(),
        week_label=week_label,
        subject=f"CFC Weekly Update — {collected.window['end_date']}",
        preheader="Fresh from the farm, available this week.",
        sections=sections,
        provenance={"collected_input": str(input)},
    )
    _write_json(out, draft.model_dump())
    typer.echo(json.dumps({"ok": True, "out": str(out), "subject": draft.subject}, indent=2))


@render_app.command("html")
def render_html(
    input: Path = typer.Option(..., "--input"),
    out: Path = typer.Option(Path("build/newsletter.html"), "--out"),
    template_dir: Path = typer.Option(Path("templates"), "--template-dir"),
    template_name: str = typer.Option("cfc-weekly.html.j2", "--template-name"),
) -> None:
    draft = Draft(**_read_json(input))
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    tpl = env.get_template(template_name)
    html = tpl.render(draft=draft.model_dump())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    typer.echo(json.dumps({"ok": True, "out": str(out), "template": template_name}, indent=2))


@check_app.command("html")
def check_html(
    input: Path = typer.Option(..., "--input"),
) -> None:
    html = input.read_text()
    required = ["<html", "<body", "NEW THIS WEEK", "Quick Notes"]
    missing = [x for x in required if x not in html]
    if missing:
        typer.echo(json.dumps({"ok": False, "missing": missing}, indent=2))
        raise typer.Exit(1)
    typer.echo(json.dumps({"ok": True, "input": str(input)}, indent=2))


@render_app.command("package")
def render_package(
    draft_input: Path = typer.Option(..., "--draft"),
    html_input: Path = typer.Option(..., "--html"),
    out: Path = typer.Option(Path("artifacts/newsletter-latest.json"), "--out"),
) -> None:
    draft = _read_json(draft_input)
    html = html_input.read_text()
    artifact = {
        "schema_version": "1.0",
        "generated_at_et": _now_et(),
        "status": "draft",
        "draft": draft,
        "html": html,
    }
    _write_json(out, artifact)
    typer.echo(json.dumps({"ok": True, "out": str(out), "status": "draft"}, indent=2))


@site_app.command("build")
def site_build(
    artifacts_dir: Path = typer.Option(Path("artifacts"), "--artifacts-dir"),
    docs_dir: Path = typer.Option(Path("docs"), "--docs-dir"),
) -> None:
    docs_dir.mkdir(parents=True, exist_ok=True)
    artifact_files = sorted(artifacts_dir.glob("*.json"), reverse=True)

    rows = []
    for f in artifact_files:
        try:
            data = _read_json(f)
            title = data.get("draft", {}).get("subject", f.stem)
            generated = data.get("generated_at_et", "")
            html_path = docs_dir / f"{f.stem}.html"
            html_path.write_text(data.get("html", ""))
            json_copy = docs_dir / f.name
            json_copy.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            rows.append({"name": f.stem, "title": title, "generated": generated})
        except Exception:
            continue

    index_lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>CFC Newsletter Artifacts</title></head><body>",
        "<h1>CFC Newsletter Artifacts</h1>",
        "<ul>",
    ]
    for r in rows:
        index_lines.append(
            f"<li><a href='{r['name']}.html'>{r['title']}</a> &mdash; {r['generated']} &mdash; <a href='{r['name']}.json'>json</a></li>"
        )
    index_lines += ["</ul>", "</body></html>"]
    (docs_dir / "index.html").write_text("\n".join(index_lines))
    typer.echo(json.dumps({"ok": True, "docs": str(docs_dir), "artifacts_indexed": len(rows)}, indent=2))


if __name__ == "__main__":
    app()
