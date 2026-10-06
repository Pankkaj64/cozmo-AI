"""Deterministic totals and the complete human-readable claim packet."""

from decimal import Decimal, ROUND_HALF_UP
from html import escape
from urllib.parse import urlparse


def total(values):
    return float(
        sum(
            (
                Decimal(str(v))
                for v in values
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ),
            Decimal(0),
        ).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    )


def calculate_totals(packet: dict) -> dict:
    books, items = packet.get("books", []), packet.get("items", [])
    replacement = [(b.get("replacement_cost") or {}).get("amount") for b in books]
    used = [(b.get("used_value") or {}).get("amount") for b in books]
    low = [(i.get("replacement_cost") or {}).get("low") for i in items]
    high = [(i.get("replacement_cost") or {}).get("high") for i in items]
    measured = [
        b["spine_thickness_cm"]
        for b in books
        if b.get("spine_thickness_cm") is not None
    ]
    packet["totals"] = {
        "book_count": len(books),
        "item_count": len(items),
        "books_identified": sum(bool(b.get("title")) for b in books),
        "books_unidentified": sum(not b.get("title") for b in books),
        "shelf_run_m": round(sum(measured) / 100, 3),
        "shelf_run_complete": len(measured) == len(books) and bool(books),
        "spines_measured": len(measured),
        "books_replacement_cost": total(replacement),
        "books_used_value": total(used),
        "items_replacement_cost_low": total(low),
        "items_replacement_cost_high": total(high),
        "excluded_from_totals": sum(
            r is None or u is None for r, u in zip(replacement, used)
        )
        + sum(l is None or h is None for l, h in zip(low, high)),
        "excluded_book_replacement": sum(v is None for v in replacement),
        "excluded_book_used": sum(v is None for v in used),
        "excluded_items": sum(v is None for v in low),
        "definition": "Known-price subtotals only. excluded_from_totals counts distinct lines missing at least one required value.",
    }
    return packet["totals"]


def text(value):
    return escape(str(value)) if value is not None and value != "" else "Unknown"


def link(url, label):
    # Reports live in data/packets; evidence refs are rooted at data/.
    if url.startswith("data/"):
        url = "../" + url.removeprefix("data/")
    elif urlparse(url).scheme not in {"http", "https"}:
        return text(label)
    return f'<a href="{escape(url, quote=True)}">{text(label)}</a>'


def price(value, amount="amount"):
    if value.get(amount) is None:
        return "Unknown / excluded"
    return (
        f"{text(value[amount])} {text(value.get('currency',''))}<br>{link(value.get('url',''), value.get('source',''))}<br>{text(value.get('retrieved_at'))}; {text(value.get('condition_assumed'))}"
        + (
            f"<br>Converted from {text(value.get('original_currency'))}; FX {text(value.get('fx_rate'))}, {link(value.get('fx_url',''), value.get('fx_date',''))}"
            if value.get("converted")
            else ""
        )
    )


def table(headers, rows):
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{escape(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
        )
        + "</tbody></table>"
    )


def report_html(packet: dict) -> str:
    """Concise inventory report; retain the complete audit in the JSON packet."""
    sweep = packet["sweep"]

    def cell(value):
        return escape(str(value)) if value is not None else ""

    from .materials import identified_materials
    exported = identified_materials(packet)["materials"]
    rows = []
    for book in exported:
        if book["type"] != "book":
            continue
        spine = book.get("spine_cm", {})
        size = f"{cell(spine['height'])} × {cell(spine['thickness'])} cm" if spine else ""
        rows.append([cell(book["id"]), cell(book.get("shelf", "")), cell(book["name"]),
                     cell(book.get("author", "")), cell(book.get("publisher", "")), cell(book.get("edition", "")),
                     cell(book["status"].replace("_", " ")), link(book["evidence"], "Open frame") if book["evidence"] else "", size])
    object_rows = []
    for item in exported:
        if item["type"] != "object":
            continue
        dims = item.get("dimensions_cm", {})
        size = " × ".join(cell(dims.get(k)) for k in ("w", "h", "d")) + " cm" if dims else ""
        object_rows.append([cell(item["id"]), cell(item["name"]), cell(item.get("material", "")),
                            cell(item.get("brand_model", "")), cell(item["status"].replace("_", " ")),
                            link(item["evidence"], "Open frame") if item["evidence"] else "", size])
    objects = "<h2>Other identified contents</h2>" + table(["ID", "Name", "Material", "Brand / model", "Status", "Evidence", "Dimensions"], object_rows) if object_rows else ""
    inventory = table(["ID", "Shelf", "Title", "Author", "Publisher", "Edition", "Status", "Evidence", "Spine (H × T)"], rows) if rows else "<p>No verified book identities yet.</p>"
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Library inventory</title>
<style>body{{font:14px system-ui;color:#172321;margin:32px}}h1{{font-size:24px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #d9e1dc;padding:9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#eef3ef}}td:first-child{{max-width:130px}}@media print{{body{{margin:10px}}thead{{display:table-header-group}}tr{{break-inside:avoid}}}}</style>
</head><body><h1>Library inventory</h1><p>{cell(sweep.get("country", ""))} · {cell(sweep.get("captured_at", ""))}</p><h2>Books</h2>{inventory}{objects}</body></html>'''
