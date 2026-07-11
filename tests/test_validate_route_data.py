import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_route_data", ROOT / "scripts" / "validate_route_data.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def episode(bvid, lat, lng, distance, *, visible=True):
    return {
        "date": "2026-07-11",
        "bvid": bvid,
        "phase": "???",
        "ride": True,
        "lat": lat,
        "lng": lng,
        "distanceKm": distance,
        "mapVisible": visible,
        "foods": [],
        "foodDetails": [],
        "lodgings": [],
        "costs": [],
        "highlights": [],
        "evidence": [],
        "riskFlags": [],
    }


class RouteValidationTests(unittest.TestCase):
    def test_visible_coordinate_conflict_fails(self):
        entries = [
            episode("BVFIRST", 11.18, 108.72, 20),
            episode("BVSECOND", 11.18, 108.72, 200),
        ]
        self.assertTrue(
            any("coordinate/distance conflict" in error for error in MODULE.validate(entries))
        )

    def test_hidden_coordinate_conflict_passes(self):
        entries = [
            episode("BVFIRST", 11.18, 108.72, 20),
            episode("BVSECOND", 11.18, 108.72, 200, visible=False),
        ]
        self.assertEqual(MODULE.validate(entries), [])

    def test_risk_flags_must_be_array(self):
        item = episode("BVARRAY", 11.18, 108.72, 20)
        item["riskFlags"] = "missing-key-timepoints"
        self.assertTrue(any("riskFlags must be an array" in x for x in MODULE.validate([item])))


if __name__ == "__main__":
    unittest.main()
