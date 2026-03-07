from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml


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


def _collect_orders_source(start_date: str, end_date: str, mcp_command: str) -> dict[str, Any]:
    proc = subprocess.run(
        [mcp_command, "orders-export", "--start-date", start_date, "--end-date", end_date],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"MCP command failed: {proc.stderr or proc.stdout}")
    envelope = json.loads(proc.stdout)
    if not envelope.get("ok"):
        raise RuntimeError(f"orders-export failed: {json.dumps(envelope)}")

    orders = _extract_orders(envelope)
    product_counter = Counter()
    vendor_counter = Counter()
    line_items = 0

    for order in orders:
        entries = order.get("order_entries") or order.get("line_items") or []
        if not isinstance(entries, list):
            entries = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            line_items += 1
            product = str(item.get("product_name") or item.get("name") or item.get("title") or "Unknown")
            vendor = str(item.get("vendor_name") or item.get("producer_name") or "Unknown")
            qty = item.get("quantity_to_charge") or item.get("unit_quantity") or item.get("quantity") or 1
            try:
                qty_f = float(qty)
            except Exception:
                qty_f = 1.0
            product_counter[product] += qty_f
            vendor_counter[vendor] += qty_f

    return {
        "source": "orders_export",
        "raw": envelope,
        "normalized": {
            "orders": len(orders),
            "line_items": line_items,
            "top_products": [{"name": k, "score": v} for k, v in product_counter.most_common(10)],
            "top_vendors": [{"name": k, "score": v} for k, v in vendor_counter.most_common(10)],
            "auth_source": envelope.get("auth_source"),
        },
    }


def _to_price_cents(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v if abs(v) > 100 else int(v * 100)
    if isinstance(v, float):
        return int(round(v * 100))
    s = str(v).replace("$", "").replace(",", "").strip()
    try:
        return int(round(float(s) * 100))
    except Exception:
        return None


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("results", "data", "products", "items"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _collect_storefront_source(storefront_url: str, category: str, token: str = "") -> dict[str, Any]:
    sep = "&" if "?" in storefront_url else "?"
    url = f"{storefront_url}{sep}{urlencode({'category': category})}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)

    with urlopen(req, timeout=30) as resp:
        raw_text = resp.read().decode("utf-8")
        payload = json.loads(raw_text)

    products = _extract_products(payload)
    normalized_products: list[dict[str, Any]] = []
    for p in products:
        name = str(p.get("name") or p.get("title") or "")
        if not name:
            continue

        categories = p.get("categories") or p.get("category") or []
        categories_s = json.dumps(categories).lower()
        if category.lower() not in categories_s and str(p.get("category", "")).lower() != category.lower():
            # Keep only explicit target category for deterministic behavior.
            continue

        image_url = p.get("image_url") or p.get("image") or p.get("primary_image_url") or ""
        price_cents = (
            _to_price_cents(p.get("price_cents"))
            or _to_price_cents(p.get("price"))
            or _to_price_cents(p.get("unit_price"))
            or _to_price_cents(p.get("base_price"))
        )

        normalized_products.append(
            {
                "name": name,
                "sku": p.get("sku") or p.get("id") or "",
                "category": category,
                "image_url": str(image_url),
                "price_cents": price_cents,
                "currency": p.get("currency") or "CAD",
            }
        )

    return {
        "source": "storefront_products",
        "request": {"url": url, "category": category},
        "raw": payload,
        "normalized": {
            "count": len(normalized_products),
            "products": normalized_products,
        },
    }


def _collect_price_list_source(mcp_command: str) -> dict[str, Any]:
    proc = subprocess.run(
        [mcp_command, "storefront-price-list"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"storefront-price-list command failed: {proc.stderr or proc.stdout}")
    envelope = json.loads(proc.stdout)
    if not envelope.get("ok"):
        raise RuntimeError(f"storefront-price-list failed: {json.dumps(envelope)}")

    pl_data = envelope.get("price_list") or {}
    products_raw = envelope.get("products") or {}
    products_payload = products_raw.get("data") if isinstance(products_raw, dict) else products_raw
    products_list = _extract_products(products_payload)

    normalized_products: list[dict[str, Any]] = []
    for p in products_list:
        name = str(p.get("name") or p.get("title") or "")
        if not name:
            continue
        vendor = p.get("vendor") or {}
        vendor_name = str(
            p.get("vendor_name")
            or p.get("producer_name")
            or (vendor.get("name") if isinstance(vendor, dict) else "")
            or ""
        )
        image_url = p.get("image_url") or p.get("image") or p.get("primary_image_url") or ""
        price_cents = (
            _to_price_cents(p.get("price_cents"))
            or _to_price_cents(p.get("price"))
            or _to_price_cents(p.get("unit_price"))
            or _to_price_cents(p.get("base_price"))
        )
        normalized_products.append({
            "name": name,
            "sku": str(p.get("sku") or p.get("id") or ""),
            "price_cents": price_cents,
            "image_url": str(image_url),
            "vendor_name": vendor_name,
            "currency": p.get("currency") or "CAD",
        })

    return {
        "source": "price_list_default",
        "raw": envelope,
        "normalized": {
            "price_list_id": pl_data.get("id"),
            "price_list_name": str(pl_data.get("name") or pl_data.get("title") or ""),
            "count": len(normalized_products),
            "products": normalized_products,
        },
    }


def run_collection(
    *,
    start_date: str,
    end_date: str,
    out_path: Path,
    collect_dir: Path,
    mcp_command: str,
    overrides_path: Path,
    storefront_url: str,
    storefront_category: str,
    storefront_token: str,
    collect_price_list: bool,
    now_et: str,
) -> dict[str, Any]:
    run_id = f"{start_date}_{end_date}"
    run_dir = collect_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    orders = _collect_orders_source(start_date, end_date, mcp_command)
    _write_json(run_dir / "orders-export.raw.json", orders["raw"])
    _write_json(run_dir / "orders-export.normalized.json", orders["normalized"])

    storefront = {"source": "storefront_products", "normalized": {"count": 0, "products": []}, "request": {"url": storefront_url, "category": storefront_category}}
    if storefront_url.strip():
        storefront = _collect_storefront_source(storefront_url=storefront_url.strip(), category=storefront_category.strip(), token=storefront_token.strip())
        _write_json(run_dir / "storefront.raw.json", storefront["raw"])
        _write_json(run_dir / "storefront.normalized.json", storefront["normalized"])

    price_list: dict[str, Any] = {"source": "price_list_default", "normalized": {"count": 0, "products": [], "price_list_id": None, "price_list_name": ""}}
    if collect_price_list:
        price_list = _collect_price_list_source(mcp_command)
        _write_json(run_dir / "price-list.raw.json", price_list["raw"])
        _write_json(run_dir / "price-list.normalized.json", price_list["normalized"])

    overrides_payload: dict[str, Any] = {}
    if overrides_path and str(overrides_path) and overrides_path.exists():
        loaded = yaml.safe_load(overrides_path.read_text())
        if isinstance(loaded, dict):
            overrides_payload = loaded
    _write_json(run_dir / "overrides.json", overrides_payload)

    sources = ["orders_export", "storefront_products", "price_list_default", "overrides"] if collect_price_list else ["orders_export", "storefront_products", "overrides"]
    collected = {
        "schema_version": "1.2",
        "generated_at_et": now_et,
        "window": {"start_date": start_date, "end_date": end_date},
        "provenance": {
            "sources": sources,
            "collect_run_dir": str(run_dir),
        },
        "metrics": {
            "orders": orders["normalized"].get("orders", 0),
            "line_items": orders["normalized"].get("line_items", 0),
            "storefront_new_products": storefront["normalized"].get("count", 0),
            "price_list_products": price_list["normalized"].get("count", 0),
        },
        "top_products": orders["normalized"].get("top_products", []),
        "top_vendors": orders["normalized"].get("top_vendors", []),
        "storefront_new_products": storefront["normalized"].get("products", []),
        "price_list_products": price_list["normalized"].get("products", []),
        "overrides": overrides_payload,
        "source_files": {
            "orders_raw": str(run_dir / "orders-export.raw.json"),
            "orders_normalized": str(run_dir / "orders-export.normalized.json"),
            "storefront_raw": str(run_dir / "storefront.raw.json") if storefront_url.strip() else None,
            "storefront_normalized": str(run_dir / "storefront.normalized.json") if storefront_url.strip() else None,
            "price_list_raw": str(run_dir / "price-list.raw.json") if collect_price_list else None,
            "price_list_normalized": str(run_dir / "price-list.normalized.json") if collect_price_list else None,
            "overrides": str(run_dir / "overrides.json"),
        },
    }

    _write_json(out_path, collected)
    return {
        "ok": True,
        "out": str(out_path),
        "collect_run_dir": str(run_dir),
        "orders": collected["metrics"]["orders"],
        "line_items": collected["metrics"]["line_items"],
        "storefront_new_products": collected["metrics"]["storefront_new_products"],
        "price_list_products": collected["metrics"]["price_list_products"],
    }
