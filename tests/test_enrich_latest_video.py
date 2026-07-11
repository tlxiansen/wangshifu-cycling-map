import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "enrich_latest_video", ROOT / "scripts" / "enrich_latest_video.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class EnrichmentTests(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(MODULE.format_timestamp(125), "02:05")
        self.assertEqual(MODULE.format_timestamp(3725), "01:02:05")

    def test_volcengine_headers_use_standard_file_asr_credentials(self):
        with patch.dict(
            "os.environ",
            {
                "VOLC_ASR_APP_ID": "test-app",
                "VOLC_ASR_ACCESS_TOKEN": "test-token",
            },
            clear=False,
        ):
            headers = MODULE.volcengine_headers("request-id")
        self.assertEqual(headers["X-Api-App-Key"], "test-app")
        self.assertEqual(headers["X-Api-Access-Key"], "test-token")
        self.assertEqual(headers["X-Api-Resource-Id"], "volc.seedasr.auc")
        self.assertEqual(headers["X-Api-Sequence"], "-1")

    def test_volcengine_utterance_timestamps_include_chunk_offset(self):
        segments = MODULE.volcengine_result_segments(
            {
                "result": {
                    "utterances": [
                        {
                            "start_time": 1500,
                            "end_time": 3200,
                            "text": "????????",
                        }
                    ]
                }
            },
            offset_seconds=600,
        )
        self.assertEqual(segments[0]["start"], 601.5)
        self.assertEqual(segments[0]["end"], 603.2)
        self.assertEqual(segments[0]["text"], "????????")

    def test_volcengine_ark_json_is_parsed(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "episode-extraction.json").read_text(
                encoding="utf-8"
            )
        )
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(fixture, ensure_ascii=False)
                    )
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )
        with patch.dict("os.environ", {"ARK_MODEL_ID": "ep-test"}, clear=False):
            parsed = MODULE.extract_structured_episode_volcengine(
                client,
                {"date": "2026-07-05", "bvid": "BVTEST", "title": "??"},
                [{"start": 15, "end": 20, "text": "????????"}],
            )
        self.assertEqual(parsed["distance_km"], 42)

    def test_candidate_selection_only_uses_recent_auto_entries(self):
        entries = [
            {"bvid": "manual", "phase": "???", "confidence": "????"},
            {
                "bvid": "old-auto",
                "phase": "Auto-added",
                "confidence": "Auto-added; pending review",
            },
            {
                "bvid": "done",
                "phase": "Auto-added",
                "confidence": "???",
                "evidence": [{"type": "ai-audio-transcript"}],
            },
            {
                "bvid": "new-auto",
                "phase": "Auto-added",
                "confidence": "Auto-added; pending review",
            },
        ]
        selected = MODULE.select_candidates(entries, lookback=3, maximum=1)
        self.assertEqual([item["bvid"] for item in selected], ["old-auto"])

    def test_merge_fixture_and_preserve_coordinates(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "episode-extraction.json").read_text(
                encoding="utf-8"
            )
        )
        entry = {
            "date": "2026-07-05",
            "bvid": "BVTEST",
            "title": "????",
            "place": "???",
            "lat": 1.23,
            "lng": 4.56,
            "confidence": "Auto-added; pending review",
            "phase": "Auto-added",
            "ride": True,
            "distanceKm": None,
            "food": "Not identified",
            "foods": [],
            "foodDetails": [],
            "lodgings": [],
            "costs": [],
            "highlights": [],
            "evidence": [],
            "rideTimeHours": None,
            "dayTimeHours": None,
            "summary": "?????",
        }
        changed = MODULE.merge_extraction(
            entry, fixture, "2026-07-05T00:00:00+00:00"
        )
        self.assertTrue(changed)
        self.assertEqual(entry["place"], "???? ? ????")
        self.assertEqual(entry["lat"], 1.23)
        self.assertEqual(entry["lng"], 4.56)
        self.assertEqual(entry["distanceKm"], 42)
        self.assertEqual(entry["highlights"][1]["time"], "02:05")
        self.assertIn("?t=125", entry["foodDetails"][0]["source"])
        self.assertEqual(entry["aiEnrichment"]["status"], "auto-extracted")

    def test_merge_updates_coordinates_from_known_vietnam_place(self):
        entry = {
            "date": "2026-07-05",
            "bvid": "BVTEST",
            "title": "??????132??????",
            "place": "?? Tuy H?a",
            "lat": 13.0955,
            "lng": 109.3209,
            "confidence": "Auto-added; pending review",
            "phase": "???",
            "ride": True,
            "distanceKm": None,
            "food": "Not identified",
            "foods": [],
            "foodDetails": [],
            "lodgings": [],
            "costs": [],
            "highlights": [],
            "evidence": [],
            "rideTimeHours": None,
            "dayTimeHours": None,
            "summary": "?????",
        }
        changed = MODULE.merge_extraction(
            entry,
            {
                "summary": "?????????",
                "ride": True,
                "distance_km": 132,
                "start_place": "?? Tuy H?a",
                "end_place": "?? Nha Trang",
                "place": "?? Tuy H?a ? ?? Nha Trang",
                "foods": [],
                "food_details": [],
                "lodgings": [],
                "costs": [],
                "highlights": [{"seconds": 60, "text": "?????"}],
                "confidence_notes": "",
            },
            "2026-07-05T00:00:00+00:00",
        )
        self.assertTrue(changed)
        self.assertEqual(entry["place"], "?? Tuy H?a ? ?? Nha Trang")
        self.assertAlmostEqual(entry["lat"], 12.2388)
        self.assertAlmostEqual(entry["lng"], 109.1967)
        self.assertEqual(entry["coordinateSource"], "local-gazetteer")
        self.assertEqual(entry["phase"], "???")
        self.assertEqual(entry["automationStatus"], "AI enriched")

    def test_structured_schema_accepts_fixture(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "episode-extraction.json").read_text(
                encoding="utf-8"
            )
        )

        class FakeResponses:
            def parse(self, **kwargs):
                model = kwargs["text_format"]
                return SimpleNamespace(output_parsed=model.model_validate(fixture))

        client = SimpleNamespace(responses=FakeResponses())
        parsed = MODULE.extract_structured_episode(
            client,
            {
                "date": "2026-07-05",
                "bvid": "BVTEST",
                "title": "??",
                "place": "???",
                "distanceKm": None,
            },
            [{"start": 15, "end": 20, "text": "????????"}],
        )
        self.assertEqual(parsed["distance_km"], 42)
        self.assertEqual(parsed["highlights"][0]["seconds"], 15)

    def test_manual_entry_is_never_overwritten(self):
        entry = {
            "bvid": "BVKEEP",
            "phase": "???",
            "confidence": "????",
            "summary": "????",
            "evidence": [],
        }
        changed = MODULE.merge_extraction(
            entry,
            {"summary": "AI ??", "highlights": []},
            "2026-07-05T00:00:00+00:00",
        )
        self.assertFalse(changed)
        self.assertEqual(entry["summary"], "????")

    def test_mui_ne_is_available_to_coordinate_lookup(self):
        coordinate = MODULE.coordinate_from_text("????????")
        self.assertEqual(coordinate["place"], "?? M?i N?")
        self.assertAlmostEqual(coordinate["lat"], 10.9330)

    def test_english_coordinate_risk_is_prioritized(self):
        entry = {
            "bvid": "BVRISK",
            "phase": "Auto-added",
            "confidence": "Auto-added; pending review",
            "riskFlags": ["coordinates-copied-from-previous-ride"],
            "ride": True,
            "distanceKm": 60,
            "highlights": [],
            "foodDetails": [],
            "food": "Not identified",
            "lodgings": [],
        }
        self.assertIn("coordinate_or_place_risk", MODULE.entry_quality_gaps(entry))

    def test_route_safety_hides_zero_movement_long_ride(self):
        entries = [
            {
                "phase": "???",
                "ride": True,
                "lat": 11.18,
                "lng": 108.72,
                "distanceKm": 20,
                "highlights": [{}],
                "lodgings": [{}],
                "confidence": "AI????",
            },
            {
                "phase": "???",
                "ride": True,
                "lat": 11.18,
                "lng": 108.72,
                "distanceKm": 60,
                "highlights": [{}],
                "lodgings": [{}],
                "confidence": "AI????",
            },
        ]
        MODULE.rebuild_quality_flags(entries)
        self.assertFalse(entries[1]["mapVisible"])
        self.assertIn("???????", entries[1]["riskFlags"])


if __name__ == "__main__":
    unittest.main()
