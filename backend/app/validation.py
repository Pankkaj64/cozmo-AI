"""Deterministic review stage, independent of inference and valuation."""


def validate_packet(packet):
    # Rebuild deterministic findings; keep perception and claimant evidence separately.
    queue = [q for q in packet["review_queue"] if q.get("origin") != "workflow"]
    def flag(ref, reason):
        queue.append({"ref_id": ref, "reason": reason, "origin": "workflow"})
    if not packet["sweep"].get("country") or not packet["sweep"].get("currency"):
        flag("locale", "Confirm country and currency before valuation.")
    for book in packet["books"]:
        if not book.get("title"):
            flag(book["id"], "Spine title unreadable or unverified. Keep as unidentified.")
        if book.get("status") == "needs_appraisal":
            flag(book["id"], "Special edition or high value: human appraisal required; prices excluded.")
        if not book.get("spine_height_cm") or not book.get("spine_thickness_cm"):
            flag(book["id"], "No calibrated spine dimensions. Mark a known reference in this saved frame.")
        for key in ("replacement_cost", "used_value"):
            if book.get(key, {}).get("amount") is None:
                flag(book["id"], f"No verified {key.replace('_', ' ')} in the selected market; excluded from this subtotal.")
    for item in packet["items"]:
        if item.get("dismissed_by_reader"):
            flag(item["id"], "Image reader suggests a structural object/person rather than claim contents; inspect and exclude if incorrect.")
        verification = item.get("crop_verification", {})
        if verification and not verification.get("agreed"):
            flag(item["id"], "Crop verification " + verification.get("status", "uncertain") + ": detector label is excluded from the identified report.")
        if not item.get("category_verified"):
            flag(item["id"], "Detector category is a proposal; verify the saved object box and exclude false detections.")
        if not item.get("material"):
            flag(item["id"], "Material unknown; review visible evidence or claimant information.")
        if item.get("replacement_cost", {}).get("low") is None:
            flag(item["id"], "No sourced replacement value; appraisal or a verified comparable is required.")
        if not all(item.get("dimensions_cm", {}).get(k) for k in ("w", "h", "d")):
            flag(item["id"], "Non-book dimensions have no recorded scale evidence.")
    if not packet["room"].get("floor_area_m2"):
        flag("room", "Room geometry missing. Supply known dimensions or calibrated geometry from this sweep.")
    if not packet.get("coverage_confirmed"):
        flag("coverage", "Complete room coverage is not confirmed. Include every shelf, wall and floor area.")
    from math import isfinite
    from urllib.parse import urlparse
    evidence = {f.get("frame_ref") for f in packet.get("frames", [])}
    seen = set()
    for line in packet["books"] + packet["items"]:
        ref = line["id"]
        if ref in seen:
            flag(ref, "Duplicate inventory ID; inspect associations.")
        seen.add(ref)
        if line.get("frame_ref") not in evidence:
            flag(ref, "Inventory line lacks a saved evidence-frame record.")
        if "title" in line and line.get("title") and line.get("id_confidence", 0) < .75 and not line.get("identity_source"):
            flag(ref, "Identification confidence below threshold; verify identity.")
        dims = [line.get("spine_height_cm"), line.get("spine_thickness_cm")] if "title" in line else list(line.get("dimensions_cm", {}).values())
        if any(v is not None and (not isinstance(v, (int, float)) or not isfinite(v) or v <= 0 or v > 10000) for v in dims):
            flag(ref, "Dimensions implausible or non-finite.")
        if "category" in line and not line.get("brand_model"):
            flag(ref, "Object brand/model unknown; use a sourced comparable range.")
        for key in (["replacement_cost", "used_value"] if "title" in line else ["replacement_cost"]):
            price = line.get(key) or {}
            if price.get("amount", price.get("low")) is None:
                continue
            url = urlparse(price.get("url", ""))
            if not price.get("source") or url.scheme not in {"http", "https"} or not url.hostname:
                flag(ref, "Price lacks a retrievable source URL and provider.")
            if not price.get("retrieved_at"):
                flag(ref, "Price retrieval date missing.")
            if price.get("currency") != packet["sweep"].get("currency"):
                flag(ref, "Price currency does not match claim currency.")
            if price.get("converted") and not all(price.get(k) for k in ("original_currency", "fx_url", "fx_date", "fx_rate")):
                flag(ref, "Converted price lacks complete FX evidence.")
    if packet["room"].get("floor_area_m2") and not packet["room"].get("scale_method"):
        flag("room", "Room measurement lacks a documented metric scale method.")
    # Keep unresolved perception findings throughout the sweep.
    excluded = set(packet.get("excluded_shelves", []))
    old_refs = {f["frame_ref"] for f in packet.get("frames", []) if f["shelf"] in excluded}
    queue = [q for q in queue if q["ref_id"] not in old_refs]
    packet["review_queue"] = list({(q["ref_id"], q["reason"]): q for q in queue}.values())
    return packet["review_queue"]
