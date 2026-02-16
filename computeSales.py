#!/usr/bin/env python3
"""
computeSales.py

CLI program that:
- Reads a product price catalogue in JSON.
- Reads a company sales record in JSON.
- Computes total cost for all sales (catalogue price * quantity).
- Prints results to console and writes them to SalesResults.txt (human readable).
- Handles invalid data gracefully: logs errors to stderr and continues.
- Includes elapsed execution time in output.

Supported input shapes:

Catalogue:
- List[dict]
- {"products": List[dict]} / {"items": List[dict]} / {"catalogue": List[dict]}
  Product dict supports keys:
  - id/name/title/product/sku (identifier)
  - price/cost/unit_price (numeric)

Sales:
A) Nested sales:
- List[sale]
- {"sales": List[sale]} / {"records": List[sale]}
  sale supports:
  - {"items": List[item]} / {"lines": List[item]}
  item supports:
  - product/title/name/id/sku (identifier)
  - quantity/qty/amount/count (numeric)

B) Flat rows (TC format):
- List[{"SALE_ID": <id>, "Product": <name>, "Quantity": <num>}, ...]
  Rows are grouped by SALE_ID into sales.

Notes about Quantity:
- Quantity can be positive or negative (e.g., returns/adjustments).
- Quantity = 0 is treated as invalid and skipped.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


RESULTS_FILENAME = "SalesResults.txt"


@dataclass(frozen=True)
class SaleLine:
    """Normalized sale line item."""
    product_key: str
    quantity: float


def eprint(message: str) -> None:
    """Print to stderr."""
    print(message, file=sys.stderr)


def load_json(path: Path) -> Optional[Any]:
    """Load JSON from file. Return None if any error occurs."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        eprint(f"[ERROR] Cannot read file '{path}': {exc}")
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        eprint(f"[ERROR] Invalid JSON in '{path}': {exc}")
        return None


def coerce_number(value: Any, context: str) -> Optional[float]:
    """Try to convert value to float. Return None if impossible."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            eprint(f"[ERROR] Non-numeric value for {context}: '{value}'")
            return None
    eprint(f"[ERROR] Invalid type for {context}: {type(value).__name__}")
    return None


def extract_catalogue_products(raw: Any) -> Iterable[Dict[str, Any]]:
    """
    Accept multiple catalogue shapes:
    - List[dict]
    - {"products": List[dict]}
    - {"items": List[dict]}
    - {"catalogue": List[dict]}
    """
    if isinstance(raw, list):
        return (x for x in raw if isinstance(x, dict))

    if isinstance(raw, dict):
        for key in ("products", "items", "catalogue"):
            value = raw.get(key)
            if isinstance(value, list):
                return (x for x in value if isinstance(x, dict))

    return []


def build_price_map(catalogue_raw: Any) -> Dict[str, float]:
    """
    Build a mapping from product identifier/name/title to price.
    Tries:
      - id keys: title/name/product/id/sku
      - price keys: price/cost/unit_price
    """
    price_map: Dict[str, float] = {}

    for idx, prod in enumerate(extract_catalogue_products(catalogue_raw), start=1):
        product_key = None
        for key in ("title", "name", "product", "id", "sku"):
            value = prod.get(key)
            if isinstance(value, str) and value.strip():
                product_key = value.strip()
                break

        if product_key is None:
            eprint(f"[ERROR] Catalogue item #{idx}: missing product identifier fields.")
            continue

        price_value = None
        for key in ("price", "cost", "unit_price"):
            if key in prod:
                price_value = prod.get(key)
                break

        context = f"price for catalogue item '{product_key}'"
        price = coerce_number(price_value, context)
        if price is None:
            continue
        if price < 0:
            eprint(f"[ERROR] Catalogue item '{product_key}': price cannot be negative.")
            continue

        price_map[product_key] = price

    return price_map


def is_flat_sales_rows(raw: Any) -> bool:
    """Detect flat sales rows list with SALE_ID/Product/Quantity."""
    if not isinstance(raw, list) or not raw:
        return False

    sample = raw[0]
    if not isinstance(sample, dict):
        return False

    required = {"SALE_ID", "Product", "Quantity"}
    return required.issubset(set(sample.keys()))


def convert_flat_rows_to_sales(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert flat rows into nested sales:
      [{"SALE_ID":1,"Product":"X","Quantity":2}, ...]
    -> [{"items":[{"product":"X","quantity":2}, ...], "_sale_id":1}, ...]
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []

    for idx, row in enumerate(raw_rows, start=1):
        sale_id_val = row.get("SALE_ID")
        if sale_id_val is None:
            eprint(f"[ERROR] Sales row #{idx}: missing SALE_ID. Skipping row.")
            continue

        sale_id = str(sale_id_val).strip()
        if not sale_id:
            eprint(f"[ERROR] Sales row #{idx}: empty SALE_ID. Skipping row.")
            continue

        if sale_id not in grouped:
            grouped[sale_id] = []
            order.append(sale_id)

        grouped[sale_id].append(row)

    sales: List[Dict[str, Any]] = []
    for sale_id in order:
        items: List[Dict[str, Any]] = []
        for row in grouped[sale_id]:
            items.append(
                {
                    "product": row.get("Product"),
                    "quantity": row.get("Quantity"),
                }
            )
        sales.append({"items": items, "_sale_id": sale_id})

    return sales


def extract_sales(raw: Any) -> List[Any]:
    """
    Accept multiple sales record shapes:
    - Flat rows list -> converted to list[sale]
    - List[sale]
    - {"sales": List[sale]}
    - {"records": List[sale]}
    """
    if is_flat_sales_rows(raw):
        return convert_flat_rows_to_sales(raw)

    if isinstance(raw, list):
        return raw

    if isinstance(raw, dict):
        for key in ("sales", "records"):
            value = raw.get(key)
            if isinstance(value, list):
                return value

    return []


def extract_sale_items(sale: Any) -> Iterable[Dict[str, Any]]:
    """
    Accept multiple sale shapes:
    - sale is List[dict] (direct list of items)
    - sale is {"items": List[dict]} or {"lines": List[dict]}
    """
    if isinstance(sale, list):
        return (x for x in sale if isinstance(x, dict))

    if isinstance(sale, dict):
        for key in ("items", "lines"):
            value = sale.get(key)
            if isinstance(value, list):
                return (x for x in value if isinstance(x, dict))

    return []


def get_sale_label(sale: Any, sale_index: int) -> str:
    """Return a user-friendly sale label (includes SALE_ID when available)."""
    if isinstance(sale, dict) and "_sale_id" in sale:
        sale_id = str(sale.get("_sale_id")).strip()
        if sale_id:
            return f"Sale #{sale_index} (SALE_ID {sale_id})"
    return f"Sale #{sale_index}"


def normalize_sale_lines(sale: Any, sale_index: int) -> List[SaleLine]:
    """
    Normalize sale items into SaleLine(product_key, quantity).
    Invalid lines are reported and skipped.

    Quantity rules:
      - qty == 0 -> invalid (skip)
      - qty < 0  -> allowed (warn, treated as return/adjustment)
    """
    lines: List[SaleLine] = []

    for line_index, item in enumerate(extract_sale_items(sale), start=1):
        product_key = None
        for key in ("product", "title", "name", "id", "sku", "Product"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                product_key = value.strip()
                break

        if product_key is None:
            eprint(
                f"[ERROR] Sale #{sale_index} line #{line_index}: missing product key."
            )
            continue

        qty_value = None
        for key in ("quantity", "qty", "amount", "count", "Quantity"):
            if key in item:
                qty_value = item.get(key)
                break

        qty_context = f"quantity for '{product_key}' in sale #{sale_index}"
        qty = coerce_number(qty_value, qty_context)
        if qty is None:
            continue

        if qty == 0:
            eprint(
                f"[ERROR] Sale #{sale_index} line #{line_index} ('{product_key}'): "
                "quantity cannot be 0. Skipping."
            )
            continue

        if qty < 0:
            eprint(
                f"[WARNING] Sale #{sale_index} line #{line_index} ('{product_key}'): "
                f"negative quantity detected ({qty}). "
                "Treating as return/adjustment."
            )

        lines.append(SaleLine(product_key=product_key, quantity=qty))

    return lines


def money(value: float) -> str:
    """Format numeric value as currency-like string."""
    return f"${value:,.2f}"


def compute_totals(
    price_map: Dict[str, float],
    sales_raw: Any,
) -> Tuple[List[str], float, int, int]:
    """
    Compute per-sale and grand totals.

    Returns:
      - rendered output lines (human readable)
      - grand_total
      - processed_sales_count
      - processed_lines_count (valid structured lines, not necessarily priced)
    """
    output: List[str] = []
    grand_total = 0.0
    processed_sales = 0
    processed_lines = 0

    sales_list = extract_sales(sales_raw)
    if not sales_list:
        eprint("[ERROR] No sales found in sales record JSON (empty or invalid shape).")

    output.append("SALES RESULTS")
    output.append("=" * 70)
    output.append("")

    header = "{:<35} {:>8} {:>12} {:>12}".format(
        "Product", "Qty", "Unit", "Line Total"
    )

    for sale_idx, sale in enumerate(sales_list, start=1):
        processed_sales += 1
        sale_label = get_sale_label(sale, sale_idx)
        lines = normalize_sale_lines(sale, sale_idx)
        sale_total = 0.0

        output.append(sale_label)
        output.append("-" * 70)
        output.append(header)
        output.append("-" * 70)

        if not lines:
            output.append("(No valid items found in this sale.)")
            output.append("")
            continue

        for line in lines:
            processed_lines += 1
            unit_price = price_map.get(line.product_key)
            if unit_price is None:
                eprint(
                    f"[ERROR] {sale_label}: unknown product '{line.product_key}' "
                    "(not found in catalogue). Skipping."
                )
                continue

            line_total = unit_price * line.quantity
            sale_total += line_total

            product_cell = f"{line.product_key[:35]:35}"
            qty_cell = f"{line.quantity:8.2f}"
            unit_cell = f"{money(unit_price):>12}"
            total_cell = f"{money(line_total):>12}"
            output.append(f"{product_cell} {qty_cell} {unit_cell} {total_cell}")

        output.append("-" * 70)
        sale_total_line = "{:>57} {:>12}".format("Sale Total", money(sale_total))
        output.append(sale_total_line)
        output.append("")

        grand_total += sale_total

    output.append("=" * 70)
    grand_total_line = "{:>57} {:>12}".format(
        "GRAND TOTAL",
        money(grand_total),
    )
    output.append(grand_total_line)
    output.append("")
    output.append(f"Processed sales: {processed_sales}")
    output.append(f"Processed item lines (valid structure): {processed_lines}")

    return output, grand_total, processed_sales, processed_lines


def write_results(lines: List[str], path: Path) -> None:
    """Write output lines to file."""
    content = "\n".join(lines) + "\n"
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        eprint(f"[ERROR] Cannot write results file '{path}': {exc}")


def main(argv: List[str]) -> int:
    """Program entrypoint."""
    if len(argv) != 3:
        eprint("Usage:\n  python computeSales.py priceCatalogue.json salesRecord.json")
        return 2

    start = time.perf_counter()

    catalogue_path = Path(argv[1])
    sales_path = Path(argv[2])

    catalogue_raw = load_json(catalogue_path)
    sales_raw = load_json(sales_path)

    if catalogue_raw is None or sales_raw is None:
        eprint("[ERROR] Cannot proceed due to invalid input file(s).")
        return 1

    price_map = build_price_map(catalogue_raw)
    if not price_map:
        eprint("[ERROR] No valid products loaded from catalogue. Totals may be zero.")

    rendered_lines, _, _, _ = compute_totals(price_map, sales_raw)

    elapsed = time.perf_counter() - start
    rendered_lines.append(f"Elapsed time: {elapsed:.6f} seconds")

    print("\n".join(rendered_lines))
    write_results(rendered_lines, Path(RESULTS_FILENAME))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
