from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import typer
import yaml
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field

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
    schema_version: str = "1.0"
    generated_at_et: str
    window: dict[str, str]
    provenance: dict[str, Any]
    metrics: dict[str, Any]
    top_products: list[dict[str, Any]] = Field(default_factory=list)
    top_vendors: list[dict[str, Any]] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)


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


def _extract_orders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("results"), list):
        return [x for x in payload["data"]["results"] if isinstance(x, dict)]
    if isinstance(payload.get("data"), list):
        return [x for x in payload["data"] if isinstance(x, dict)]
    if isinstance(payload.get("results"), list):
        return [x for x in payload["results"] if isinstance(x, dict)]
    return []


@data_app.command("collect")
def data_collect(
    start_date: str = typer.Option(..., "--start-date", help="YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end-date", help="YYYY-MM-DD"),
    out: Path = typer.Option(Path("build/collected.json"), "--out"),
    mcp_command: str = typer.Option("mcp-localline", "--mcp-command"),
    overrides: Path = typer.Option(Path(""), "--overrides", help="Optional YAML overrides file"),
) -> None:
    proc = subprocess.run(
        [mcp_command, "orders-export", "--start-date", start_date, "--end-date", end_date],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise typer.BadParameter(f"MCP command failed: {proc.stderr or proc.stdout}")

    envelope = json.loads(proc.stdout)
    if not envelope.get("ok"):
        raise typer.BadParameter(f"orders-export failed: {json.dumps(envelope)}")

    orders = _extract_orders(envelope)
    product_counter = Counter()
    vendor_counter = Counter()
    order_entries = 0

    for order in orders:
        entries = order.get("order_entries") or order.get("line_items") or []
        if not isinstance(entries, list):
            entries = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            order_entries += 1
            product = str(item.get("product_name") or item.get("name") or item.get("title") or "Unknown")
            vendor = str(item.get("vendor_name") or item.get("producer_name") or "Unknown")
            qty = item.get("quantity_to_charge") or item.get("unit_quantity") or item.get("quantity") or 1
            try:
                qty_f = float(qty)
            except Exception:
                qty_f = 1.0
            product_counter[product] += qty_f
            vendor_counter[vendor] += qty_f

    top_products = [{"name": k, "score": v} for k, v in product_counter.most_common(10)]
    top_vendors = [{"name": k, "score": v} for k, v in vendor_counter.most_common(10)]

    overrides_payload: dict[str, Any] = {}
    if overrides and str(overrides) and overrides.exists():
        loaded = yaml.safe_load(overrides.read_text())
        if isinstance(loaded, dict):
            overrides_payload = loaded

    collected = CollectedInput(
        generated_at_et=_now_et(),
        window={"start_date": start_date, "end_date": end_date},
        provenance={
            "source": "mcp-localline orders-export",
            "command": [mcp_command, "orders-export", "--start-date", start_date, "--end-date", end_date],
            "auth_source": envelope.get("auth_source"),
        },
        metrics={"orders": len(orders), "line_items": order_entries},
        top_products=top_products,
        top_vendors=top_vendors,
        overrides=overrides_payload,
    )
    _write_json(out, collected.model_dump())
    typer.echo(json.dumps({"ok": True, "out": str(out), "orders": len(orders), "line_items": order_entries}, indent=2))


@content_app.command("draft")
def content_draft(
    input: Path = typer.Option(..., "--input"),
    out: Path = typer.Option(Path("build/draft.json"), "--out"),
) -> None:
    collected = CollectedInput(**_read_json(input))
    week_label = f"{collected.window['start_date']} to {collected.window['end_date']}"

    featured = collected.overrides.get("featured_products") if isinstance(collected.overrides, dict) else None
    if not isinstance(featured, list) or not featured:
        featured = [p["name"] for p in collected.top_products[:5]]

    notes = []
    if isinstance(collected.overrides, dict) and isinstance(collected.overrides.get("notes"), list):
        notes = [str(x) for x in collected.overrides["notes"]]

    sections = [
        {
            "title": "This Week at County Farm Collective",
            "items": notes or [
                f"We processed {collected.metrics.get('orders', 0)} orders this week.",
                f"Top line activity across {collected.metrics.get('line_items', 0)} line items.",
            ],
        },
        {
            "title": "Featured Products",
            "items": featured,
        },
        {
            "title": "Vendor Highlights",
            "items": [f"{v['name']}" for v in collected.top_vendors[:5]],
        },
    ]

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
    required = ["<html", "<body", "Featured Products", "Vendor Highlights"]
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
