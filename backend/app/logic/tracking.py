"""Appearance and scene-alignment tracking of objects across sampled frames."""

from __future__ import annotations

import base64
import io
import re
from difflib import SequenceMatcher

import cv2
import numpy as np
from PIL import Image, ImageOps


def normalized(value):
    return " ".join(re.findall(r"\w+", str(value).casefold()))


def add_signatures(raw, objects):
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    for obj in objects:
        box = obj.get("bbox")
        if not box:
            continue
        crop = image.crop(
            tuple(int(v * (image.width if i % 2 == 0 else image.height)) for i, v in enumerate(box))
        )
        if min(crop.size) < 2:
            continue
        crop.thumbnail((320, 320))
        rgb = np.array(crop)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        small = cv2.resize(gray, (9, 8))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).flatten()
        hist /= max(float(hist.sum()), 1)
        keys, desc = cv2.ORB_create(nfeatures=160).detectAndCompute(gray, None)
        obj["appearance"] = {
            "hash": np.packbits(small[:, 1:] > small[:, :-1]).tobytes().hex(),
            "hist": [round(float(x), 5) for x in hist],
            "descriptors": (base64.b64encode(desc.tobytes()).decode() if desc is not None else ""),
            "points": [
                [round(k.pt[0] / crop.width, 4), round(k.pt[1] / crop.height, 4)] for k in keys
            ],
        }
    return objects


def appearance_score(a, b):
    if not a or not b:
        return 0
    distance = (int(a["hash"], 16) ^ int(b["hash"], 16)).bit_count() / 64
    hist = sum(min(x, y) for x, y in zip(a["hist"], b["hist"], strict=False))
    if distance < 0.17 and hist > 0.8:
        return 0.88
    try:
        da = np.frombuffer(base64.b64decode(a["descriptors"]), np.uint8).reshape(-1, 32)
        db = np.frombuffer(base64.b64decode(b["descriptors"]), np.uint8).reshape(-1, 32)
        if min(len(da), len(db)) >= 8:
            matches = [
                m
                for pair in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
                if len(pair) == 2
                for m, n in [pair]
                if m.distance < 0.7 * n.distance
            ]
            if len(matches) >= 8:
                pa = np.float32([a["points"][m.queryIdx] for m in matches])
                pb = np.float32([b["points"][m.trainIdx] for m in matches])
                _, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 0.035)
                if mask is not None and int(mask.sum()) >= 8 and float(mask.mean()) >= 0.65:
                    return 0.95
    except (ValueError, KeyError, cv2.error):
        pass
    return 0


def match_track(found, available, kind, transforms=None):
    from .perception import overlap

    scored = []
    title = normalized(found.get("title") or found.get("proposed_title"))
    for old in available:
        old_title = normalized(old.get("title") or old.get("proposed_title"))
        score = 0
        if kind == "book" and title and old_title:
            similarity = SequenceMatcher(None, title, old_title).ratio()
            if (
                title == old_title
                or similarity >= 0.8
                or (
                    min(len(title), len(old_title)) >= 4
                    and (title in old_title or old_title in title)
                )
            ):
                score = 0.92
        signatures = [old.get("appearance")] + [
            o.get("appearance") for o in old.get("observations", [])[-3:]
        ]
        score = max(
            [score] + [appearance_score(found.get("appearance"), sig) for sig in signatures]
        )
        # Stationary objects with no appearance evidence (legacy packets/tests).
        if (
            kind == "item"
            and found.get("category") == old.get("category")
            and not found.get("appearance")
            and not old.get("appearance")
        ):
            a, b = found.get("bbox"), old.get("object_bbox")
            if a and b and overlap(a, b) >= 0.65:
                score = 0.85
        if kind == "item" and transforms is not None:
            views = [{"bbox": old.get("object_bbox"), "frame_ref": old.get("frame_ref")}] + old.get(
                "observations", []
            )[-5:]
            alignment = max(
                projected_overlap(
                    view.get("bbox"),
                    found.get("bbox"),
                    transforms.get(view.get("frame_ref")),
                )
                for view in views
            )
            if alignment >= 0.55 and (
                old.get("category") == found.get("category") or alignment >= 0.78
            ):
                score = max(score, 0.9 + 0.09 * alignment)
        if score >= 0.85:
            scored.append((score, old))
    return max(scored, key=lambda pair: pair[0])[1] if scored else None


def retain_observation(collection, previous, entry, found, ref):
    observation = {
        key: found[key]
        for key in (
            "bbox",
            "confidence",
            "title",
            "author",
            "publisher",
            "category",
            "reader_category",
            "appearance",
            "ocr_lines",
        )
        if key in found
    }
    observation["frame_ref"] = ref
    if not previous:
        entry["observations"] = [observation]
        collection.append(entry)
        return entry
    previous.setdefault("observations", []).append(observation)
    previous["last_seen_frame"] = ref
    if (
        not previous.get("identity_source")
        and found.get("reader_category")
        and (not previous.get("reader_category") or previous.get("dismissed_by_reader"))
    ):
        for key in (
            "category",
            "description",
            "reader_category",
            "proposed_material",
            "proposed_brand_model",
            "dismissed_by_reader",
        ):
            if key in entry:
                previous[key] = entry[key]
    if not previous.get("identity_source") and entry.get("crop_verification"):
        previous["crop_verification"] = entry["crop_verification"]
        if "category" in previous:
            previous["category_verified"] = bool(
                entry["crop_verification"].get("agreed")
            ) and previous["category"] == entry.get("category")
        elif (
            not previous.get("title")
            and entry.get("title")
            and entry["crop_verification"].get("agreed")
        ):
            for key in (
                "title",
                "author",
                "publisher",
                "edition",
                "isbn",
                "status",
                "id_confidence",
                "reader_evidence",
            ):
                if key in entry:
                    previous[key] = entry[key]

    # Preserve reviewed identity, prices, and the evidence plane of measurements.
    def quality(item):
        title = item.get("proposed_title") or item.get("title") or ""
        return (
            bool(title),
            bool(item.get("proposed_author")),
            bool(item.get("proposed_publisher")),
            len(title),
            sum(len(x.get("text", "")) for x in item.get("ocr_lines", [])),
            item.get("id_confidence", item.get("confidence", 0)),
        )

    if (
        not previous.get("identity_source")
        and not previous.get("measurement")
        and previous.get("bbox_kind") != "spine"
        and quality(entry) > quality(previous)
    ):
        for key in (
            "frame_ref",
            "bbox",
            "bbox_kind",
            "object_bbox",
            "appearance",
            "ocr_lines",
            "proposed_title",
            "proposed_author",
            "proposed_publisher",
            "description",
            "partial",
            "reader_evidence",
            "id_confidence",
            "confidence",
            "proposed_material",
            "proposed_brand_model",
            "reader_category",
            "dismissed_by_reader",
        ):
            if key in entry:
                previous[key] = entry[key]
    return previous


def scene_signature(raw):
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    image.thumbnail((800, 800))
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    keys, desc = cv2.ORB_create(nfeatures=700).detectAndCompute(gray, None)
    return {
        "descriptors": (base64.b64encode(desc.tobytes()).decode() if desc is not None else ""),
        "points": [
            [round(k.pt[0] / image.width, 5), round(k.pt[1] / image.height, 5)] for k in keys
        ],
    }


def scene_transform(old, current):
    """Only trust spatially distributed background matches, not one moving cover."""
    try:
        da = np.frombuffer(base64.b64decode(old["descriptors"]), np.uint8).reshape(-1, 32)
        db = np.frombuffer(base64.b64decode(current["descriptors"]), np.uint8).reshape(-1, 32)
        if min(len(da), len(db)) < 20:
            return None
        matches = [
            m
            for pair in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
            if len(pair) == 2
            for m, n in [pair]
            if m.distance < 0.7 * n.distance
        ]
        if len(matches) < 20:
            return None
        pa = np.float32([old["points"][m.queryIdx] for m in matches])
        pb = np.float32([current["points"][m.trainIdx] for m in matches])
        matrix, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 0.012)
        if mask is None or matrix is None or int(mask.sum()) < 18 or float(mask.mean()) < 0.35:
            return None
        spread = np.ptp(pa[mask.flatten().astype(bool)], axis=0)
        if spread[0] < 0.35 or spread[1] < 0.3:
            return None
        return matrix
    except (KeyError, ValueError, cv2.error):
        return None


def projected_overlap(box, current_box, matrix):
    from .perception import overlap

    if not box or not current_box or matrix is None:
        return 0
    x, y, r, b = box
    points = cv2.perspectiveTransform(np.float32([[[x, y], [r, y], [r, b], [x, b]]]), matrix)[0]
    low, high = points.min(axis=0), points.max(axis=0)
    return overlap([float(low[0]), float(low[1]), float(high[0]), float(high[1])], current_box)
