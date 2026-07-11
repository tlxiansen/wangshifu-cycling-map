#!/usr/bin/env python3
"""Fail the update before publishing structurally unsafe route data."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARRAY_FIELDS = (
    "foods",
    "foodDetails",
    "lodgings",
    "costs",
    "highlights",
    "evidence",
    "riskFlags",
)


def haversine_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    earth = 6371.0
    lat1, lng1 = math.radians(float(a["lat"])), math.radians(float(a["lng"]))
    lat2, lng2 = math.radians(float(b["lat"])), math.radians(float(b["lng"]))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return 2 * earth * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def validate(entries: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    previous_by_phase: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        label = f"entry[{index}] {entry.get('date', '?')} {entry.get('bvid', '?')}"
        bvid = str(entry.get("bvid") or "")
        if not bvid.startswith("BV"):
            errors.append(f"{label}: invalid BV id")
        elif bvid in seen:
            errors.append(f"{label}: duplicate BV id")
        seen.add(bvid)

        for field in ARRAY_FIELDS:
            if not isinstance(entry.get(field), list):
                errors.append(f"{label}: {field} must be an array")

        lat, lng = entry.get("lat"), entry.get("lng")
        if lat is not None and not -90 <= float(lat) <= 90:
            errors.append(f"{label}: latitude out of range")
        if lng is not None and not -180 <= float(lng) <= 180:
            errors.append(f"{label}: longitude out of range")

        if not entry.get("ride") or lat is None or lng is None:
            continue
        phase = str(entry.get("phase") or "")
        previous = previous_by_phase.get(phase)
        distance = entry.get("distanceKm")
        if previous and distance is not None and float(distance) > 0:
            straight = haversine_km(previous, entry)
            reported = float(distance)
            conflict = (straight < 1 and reported > 20) or straight > max(
                reported * 2.2, reported + 80
            )
            if conflict and entry.get("mapVisible") is not False:
                errors.append(
                    f"{label}: coordinate/distance conflict must be hidden "
                    f"(straight={straight:.1f}, reported={reported:.1f})"
                )
        if entry.get("mapVisible") is not False:
            previous_by_phase[phase] = entry
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "wangshifu-data.json")
    args = parser.parse_args()
    entries = json.loads(args.data.read_text(encoding="utf-8-sig"))
    if not isinstance(entries, list):
        raise ValueError("route data must be a JSON array")
    errors = validate(entries)
    if errors:
        print("Route data validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Route data validation passed: {len(entries)} episode(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
