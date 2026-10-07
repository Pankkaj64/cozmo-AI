"""Human-readable HTML report rendered from the contract packet."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from .claim import build_claim_packet


def cell(value, unknown: str = "") -> str:
    if value is None or value == "":
        return unknown
    if isinstance(value, float):
        return escape(f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}")
    return escape(str(value))


def link(url: str, label: str) -> str:
    # Reports live in data/claims/<id>/ or data/packets/; evidence refs are rooted at data/.
    if url.startswith("data/"):
        url = "../../" + url.removeprefix("data/")
    elif urlparse(url).scheme not in {"http", "https"}:
        return cell(label)
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{cell(label)}</a>'


def frame_link(ref: str) -> str:
    return link(ref, "frame") if ref else ""


def price_cell(price: dict, currency: str, amount_key: str = "amount") -> str:
    amount = price.get(amount_key)
    if amount is None:
        return '<span class="muted">not priced</span>'
    high = price.get("high")
    shown = (
        f"{cell(amount)}–{cell(high)}"
        if amount_key == "low" and high is not None and high != amount
        else cell(amount)
    )
    parts = [f"<strong>{shown} {escape(currency)}</strong>"]
    if price.get("converted"):
        parts.append(
            f'<span class="tag">converted from {cell(price.get("original_currency"))} @ {cell(price.get("fx_rate"))}</span>'
        )
    parts.append(link(price.get("url", ""), price.get("source", "source")))
    meta = " · ".join(
        filter(None, [cell(price.get("retrieved_at")), cell(price.get("condition_assumed"))])
    )
    if meta:
        parts.append(f'<span class="muted">{meta}</span>')
    if price.get("verification"):
        parts.append(
            f'<span class="muted">{cell(price["verification"]).replace("_", " ")}'
            + (
                f", match {cell(price.get('match_score'))}"
                if price.get("match_score") not in (None, "")
                else ""
            )
            + "</span>"
        )
    return "<br>".join(parts)


def table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f"<p class='muted'>{escape(empty)}</p>"
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def kv(rows: list[tuple[str, str]]) -> str:
    return (
        "<table class='kv'>"
        + "".join(f"<tr><th>{escape(k)}</th><td>{v}</td></tr>" for k, v in rows)
        + "</table>"
    )


def _area(m2, ft2) -> str:
    if m2 is None:
        return '<span class="muted">unknown</span>'
    return f"{cell(m2)} m² ({cell(ft2)} ft²)" if ft2 is not None else f"{cell(m2)} m²"


def report_html(packet: dict) -> str:
    claim = build_claim_packet(packet)
    sweep, room, totals, currency = (
        claim["sweep"],
        claim["room"],
        claim["totals"],
        claim["sweep"]["currency"],
    )
    reasons: dict[str, list[str]] = {}
    for finding in claim["review_queue"]:
        reasons.setdefault(finding["ref_id"], []).append(finding["reason"])

    summary = kv(
        [
            ("Sweep", cell(sweep["id"])),
            (
                "Captured",
                f"{cell(sweep['captured_at'])} · {cell(sweep['device'])} · {cell(sweep['duration_s'])} s sweep",
            ),
            ("Country / currency", f"{cell(sweep['country'])} / {cell(currency)}"),
            (
                "Time to packet",
                f"{cell(sweep['time_to_packet_s'], 'not recorded')} s after capture stopped",
            ),
            (
                "Books",
                f"{totals['book_count']} counted · {totals['books_identified']} identified · {totals['books_unidentified']} unidentified · {totals['books_needs_appraisal']} need appraisal",
            ),
            (
                "Shelf run",
                f"{cell(totals['shelf_run_m'])} m from {totals['spines_measured']} measured spines"
                + ("" if totals["shelf_run_complete"] else " (incomplete)"),
            ),
            (
                "Books replacement / used",
                f"{cell(totals['books_replacement_cost'])} / {cell(totals['books_used_value'])} {escape(currency)}",
            ),
            (
                "Items replacement range",
                f"{cell(totals['items_replacement_cost_low'])} – {cell(totals['items_replacement_cost_high'])} {escape(currency)} across {totals['item_count']} items",
            ),
            (
                "Excluded from totals",
                f"{totals['excluded_from_totals']} lines without a sourced value",
            ),
            ("Review queue", f"{len(claim['review_queue'])} findings"),
        ]
    )

    room_table = kv(
        [
            (
                "Dimensions (L × W × H)",
                f"{cell(room['length_m'], '?')} × {cell(room['width_m'], '?')} × {cell(room['height_m'], '?')} m",
            ),
            ("Floor area", _area(room["floor_area_m2"], room["floor_area_ft2"])),
            ("Wall area (gross)", _area(room["wall_area_m2"], room["wall_area_ft2"])),
            (
                "Wall area covered by shelving",
                _area(room["shelved_wall_area_m2"], room["shelved_wall_area_ft2"]),
            ),
            ("Shape", cell(room["shape"], "not recorded")),
            (
                "Scale method",
                cell(room["scale_method"], "none recorded")
                + (f" — {cell(room['source'])}" if room["source"] else ""),
            ),
            ("Confidence", cell(room["confidence"])),
            ("Evidence", frame_link(room["frame_ref"])),
            ("Assumptions", cell(room["assumptions"])),
        ]
    )

    book_rows = []
    for book in claim["books"]:
        spine = (
            f"{cell(book['spine_height_cm'])} × {cell(book['spine_thickness_cm'])} cm"
            if book["spine_height_cm"] is not None and book["spine_thickness_cm"] is not None
            else '<span class="muted">not measured</span>'
        )
        book_rows.append(
            [
                f'{cell(book["shelf"])}<br><span class="muted">#{book["position"]}</span>',
                (
                    f"<strong>{cell(book['title'])}</strong>"
                    if book["title"]
                    else '<span class="muted">unidentified book</span>'
                )
                + (f"<br>{cell(book['author'])}" if book["author"] else "")
                + (
                    f'<br><span class="muted">{cell(book["publisher"])}</span>'
                    if book["publisher"]
                    else ""
                )
                + (
                    f'<br><span class="muted">{cell(book["edition"])}</span>'
                    if book["edition"]
                    else ""
                )
                + (
                    f'<br><span class="muted">ISBN {cell(book["isbn"])}</span>'
                    if book["isbn"]
                    else ""
                ),
                f'{cell(book["status"]).replace("_", " ")}<br><span class="muted">conf. {cell(book["id_confidence"])}</span>',
                spine,
                price_cell(book["replacement_cost"], currency),
                price_cell(book["used_value"], currency),
                frame_link(book["frame_ref"]),
                "<br>".join(cell(r) for r in reasons.get(book["id"], [])),
            ]
        )

    item_rows = []
    for item in claim["items"]:
        dims = item["dimensions_cm"]
        size = (
            f"{cell(dims['w'])} × {cell(dims['h'])} × {cell(dims['d'])} cm"
            if all(dims[k] is not None for k in ("w", "h", "d"))
            else '<span class="muted">not measured</span>'
        )
        item_rows.append(
            [
                f"<strong>{cell(item['category'])}</strong>"
                + (f"<br>{cell(item['description'])}" if item["description"] else ""),
                cell(item["material"], "—"),
                cell(item["brand_model"], "—"),
                size,
                f'{cell(item["status"]).replace("_", " ")}<br><span class="muted">conf. {cell(item["confidence"])}</span>',
                price_cell(item["replacement_cost"], currency, "low"),
                frame_link(item["frame_ref"]),
                "<br>".join(cell(r) for r in reasons.get(item["id"], [])),
            ]
        )

    comparison = claim.get("locale_comparison")
    comparison_html = ""
    if comparison:
        base_cur, other_cur = comparison["base"]["currency"], comparison["comparison"]["currency"]
        rows = []
        for row in comparison["rows"]:

            def fig(block, key):
                value = (block or {}).get(key)
                if not value:
                    return '<span class="muted">not priced</span>'
                return (
                    f"<strong>{cell(value['amount'])} {escape(value['currency'])}</strong>"
                    + (' <span class="tag">converted</span>' if value["converted"] else "")
                    + f'<br>{link(value["url"], value["source"])}<br><span class="muted">{cell(value["retrieved_at"])}</span>'
                )

            rows.append(
                [
                    f"<strong>{cell(row['title'])}</strong>"
                    + (f"<br>{cell(row['author'])}" if row["author"] else ""),
                    fig(row.get(base_cur), "replacement_cost"),
                    fig(row.get(base_cur), "used_value"),
                    fig(row.get(other_cur), "replacement_cost"),
                    fig(row.get(other_cur), "used_value"),
                ]
            )
        comparison_html = (
            f"<h2>Second-country comparison: {escape(comparison['base']['country'])} vs {escape(comparison['comparison']['country'])}</h2>"
            f'<p class="muted">{escape(comparison["note"])} Generated {cell(comparison["generated_at"])}.</p>'
            + table(
                [
                    "Book",
                    f"Replacement ({base_cur})",
                    f"Used ({base_cur})",
                    f"Replacement ({other_cur})",
                    f"Used ({other_cur})",
                ],
                rows,
                "No identified books to compare.",
            )
        )

    performance = claim.get("performance", {})
    stage_rows = [
        [
            escape(name),
            cell(s.get("calls")),
            cell(s.get("mean_s")),
            cell(s.get("max_s")),
            cell(s.get("total_s")),
        ]
        for name, s in sorted(performance.get("stages", {}).items())
    ]
    cost = claim.get("cost", {})
    cost_rows = [
        [escape(name), cell(usage.get("calls")), cell(usage.get("elapsed_s"))]
        for name, usage in sorted(cost.get("provider_calls", {}).items())
    ]

    review_rows = [[cell(q["ref_id"]), cell(q["reason"])] for q in claim["review_queue"]]
    methodology = kv(
        [(k.replace("_", " ").capitalize(), cell(v)) for k, v in claim["methodology"].items()]
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Contents claim packet — {cell(sweep["id"])[:8]}</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;color:#1b2420;margin:32px;max-width:1400px}}
h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:18px;margin:28px 0 8px;border-bottom:1px solid #d9e1dc;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px;margin:6px 0}}
th,td{{border:1px solid #d9e1dc;padding:7px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#eef3ef;font-weight:600}}table.kv th{{width:230px}}
.muted{{color:#6b7772}}.tag{{display:inline-block;background:#fff4d6;border:1px solid #e8c97a;border-radius:4px;padding:0 5px;font-size:11px}}
a{{color:#15563f}}
@media print{{body{{margin:10px}}thead{{display:table-header-group}}tr{{break-inside:avoid}}a{{color:inherit;text-decoration:none}}}}
</style></head><body>
<h1>Library contents claim packet</h1>
<p class="muted">Generated from claim_packet.json. Every figure is traceable to a saved frame and a dated price source; blanks mean the system does not know.</p>
<h2>Summary</h2>{summary}
<h2>Room</h2>{room_table}
<h2>Books ({totals["book_count"]})</h2>
{table(["Shelf", "Work", "Status", "Spine H × T", f"Replacement ({currency})", f"Used value ({currency})", "Evidence", "Review"], book_rows, "No books recorded.")}
<h2>Non-book items ({totals["item_count"]})</h2>
{table(["Item", "Material", "Brand / model", "Dimensions W × H × D", "Status", f"Replacement ({currency})", "Evidence", "Review"], item_rows, "No non-book items recorded.")}
{comparison_html}
<h2>Review queue ({len(review_rows)})</h2>
{table(["Reference", "Reason"], review_rows, "Nothing flagged.")}
<h2>Latency per stage and cost</h2>
{table(["Stage", "Calls", "Mean s", "Max s", "Total s"], stage_rows, "No stage timings recorded.")}
{table(["Price / FX provider", "Calls", "Elapsed s"], cost_rows, "No external price or FX calls were made.")}
<p class="muted">{cell(cost.get("basis"))} {cell(performance.get("note"))}</p>
<h2>Methodology</h2>{methodology}
</body></html>"""


def claim_bundle(packet: dict, root) -> bytes:
    root = Path(root).resolve()
    sweep_id = packet["sweep"]["id"]
    output = io.BytesIO()
    missing, manifest = [], []
    refs = {f.get("frame_ref", "") for f in packet.get("frames", [])}
    refs.update(line.get("frame_ref", "") for line in packet["books"] + packet["items"])
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"data/claims/{sweep_id}/claim_packet.json",
            json.dumps(build_claim_packet(packet), indent=2),
        )
        archive.writestr(f"data/claims/{sweep_id}/report.html", report_html(packet))
        for ref in sorted(refs - {""}):
            path = (root / ref).resolve()
            if not ref.startswith("data/frames/") or not path.is_relative_to(
                root / "data" / "frames"
            ):
                raise ValueError("Evidence must be inside data/frames")
            if not path.is_file():
                missing.append(ref)
                continue
            raw = path.read_bytes()
            archive.writestr(ref, raw)
            manifest.append(
                {
                    "path": ref,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        for suffix, name in (
            ("ground_truth", "ground_truth.json"),
            ("results", "results.json"),
        ):
            path = root / "data" / "packets" / f"{sweep_id}.{suffix}.json"
            if path.is_file() and path.resolve().is_relative_to(root / "data" / "packets"):
                archive.writestr(name, path.read_bytes())
        archive.writestr(
            "manifest.json",
            json.dumps(
                {"sweep_id": sweep_id, "evidence": manifest, "missing": missing},
                indent=2,
            ),
        )
    return output.getvalue()
