import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


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

    def test_volcengine_subtitle_headers_use_bearer_token(self):
        with patch.dict(
            "os.environ",
            {"VOLC_ASR_ACCESS_TOKEN": "  test-\r\ntoken  "},
            clear=True,
        ):
            headers = MODULE.volcengine_subtitle_headers("audio/mp3")
        self.assertEqual(headers["Authorization"], "Bearer; test-token")
        self.assertEqual(headers["Content-Type"], "audio/mp3")

    def test_volcengine_subtitle_uploads_binary_and_queries_timestamps(self):
        submit = Mock()
        submit.json.return_value = {
            "code": "0",
            "message": "Success",
            "id": "task-123",
        }
        query = Mock()
        query.json.return_value = {
            "code": 0,
            "message": "Success",
            "utterances": [
                {"start_time": 1500, "end_time": 3200, "text": "今天骑了40公里"}
            ],
        }
        session = Mock()
        session.post.return_value = submit
        session.get.return_value = query

        with tempfile.TemporaryDirectory() as temporary:
            chunk = Path(temporary) / "chunk-000.mp3"
            chunk.write_bytes(b"fake-mp3")
            with patch.dict(
                "os.environ",
                {
                    "VOLC_ASR_APP_ID": "test-app",
                    "VOLC_ASR_ACCESS_TOKEN": "test-token",
                },
                clear=False,
            ):
                segments = MODULE.transcribe_chunks_volcengine_subtitle(
                    [chunk], 600, session=session
                )

        self.assertEqual(segments[0]["start"], 1.5)
        self.assertEqual(segments[0]["end"], 3.2)
        submit_kwargs = session.post.call_args.kwargs
        self.assertEqual(submit_kwargs["data"], b"fake-mp3")
        self.assertEqual(submit_kwargs["params"]["appid"], "test-app")
        self.assertEqual(submit_kwargs["headers"]["Content-Type"], "audio/mp3")
        self.assertEqual(session.get.call_args.kwargs["params"]["id"], "task-123")

    def test_volcengine_utterance_timestamps_include_chunk_offset(self):
        segments = MODULE.volcengine_result_segments(
            {
                "result": {
                    "utterances": [
                        {
                            "start_time": 1500,
                            "end_time": 3200,
                            "text": "今天骑了四十公里",
                        }
                    ]
                }
            },
            offset_seconds=600,
        )
        self.assertEqual(segments[0]["start"], 601.5)
        self.assertEqual(segments[0]["end"], 603.2)
        self.assertEqual(segments[0]["text"], "今天骑了四十公里")

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
                {"date": "2026-07-05", "bvid": "BVTEST", "title": "测试"},
                [{"start": 15, "end": 20, "text": "从测试起点出发。"}],
            )
        self.assertEqual(parsed["distance_km"], 42)

    def test_deepseek_json_is_parsed(self):
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
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return response

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )
        with patch.dict(
            "os.environ",
            {"TEXT_AI_PROVIDER": "deepseek", "DEEPSEEK_MODEL": "deepseek-chat"},
            clear=False,
        ):
            parsed = MODULE.extract_structured_episode(
                client,
                {"date": "2026-07-20", "bvid": "BVTEST", "title": "测试"},
                [{"start": 15, "end": 20, "text": "今天没有骑行。"}],
            )
        self.assertEqual(parsed["distance_km"], 42)
        self.assertEqual(captured["model"], "deepseek-chat")
        self.assertEqual(captured["response_format"], {"type": "json_object"})

    def test_encrypted_transcript_cache_roundtrip(self):
        entry = {
            "date": "2026-07-20",
            "bvid": "BVTEST123",
            "title": "测试视频",
        }
        segments = [
            {"start": 12.5, "end": 18.0, "text": "今天骑了四十公里。"}
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                "os.environ",
                {
                    "TRANSCRIPT_CACHE_DIR": temporary,
                    "TRANSCRIPT_ENCRYPTION_KEY": "unit-test-secret",
                    "ASR_PROVIDER": "volcengine",
                },
                clear=False,
            ):
                path = MODULE.save_transcript_cache(entry, segments)
                contents = path.read_bytes()
                loaded = MODULE.load_transcript_cache(entry)
        self.assertNotIn("今天骑了四十公里".encode("utf-8"), contents)
        self.assertEqual(loaded, segments)

    def test_wrong_transcript_key_is_rejected(self):
        entry = {"bvid": "BVTEST456", "title": "测试视频"}
        segments = [{"start": 0, "end": 1, "text": "测试字幕"}]
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                "os.environ",
                {
                    "TRANSCRIPT_CACHE_DIR": temporary,
                    "TRANSCRIPT_ENCRYPTION_KEY": "correct-key",
                },
                clear=False,
            ):
                MODULE.save_transcript_cache(entry, segments)
            with patch.dict(
                "os.environ",
                {
                    "TRANSCRIPT_CACHE_DIR": temporary,
                    "TRANSCRIPT_ENCRYPTION_KEY": "wrong-key",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "TRANSCRIPT_ENCRYPTION_KEY"):
                    MODULE.load_transcript_cache(entry)

    def test_deepseek_configuration_is_required(self):
        with patch.dict(
            "os.environ",
            {
                "ASR_PROVIDER": "volcengine",
                "VOLC_ASR_API_KEY": "asr-key",
                "TEXT_AI_PROVIDER": "deepseek",
                "TRANSCRIPT_ENCRYPTION_KEY": "cache-key",
            },
            clear=True,
        ):
            self.assertEqual(MODULE.missing_configuration(), ["DEEPSEEK_API_KEY"])

    def test_candidate_selection_only_uses_recent_auto_entries(self):
        entries = [
            {"bvid": "manual", "phase": "第一段", "confidence": "人工核验"},
            {
                "bvid": "old-auto",
                "phase": "Auto-added",
                "confidence": "Auto-added; pending review",
            },
            {
                "bvid": "done",
                "phase": "Auto-added",
                "confidence": "待核验",
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
            "title": "测试视频",
            "place": "前一站",
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
            "summary": "测试视频。",
        }
        changed = MODULE.merge_extraction(
            entry, fixture, "2026-07-05T00:00:00+00:00"
        )
        self.assertTrue(changed)
        self.assertEqual(entry["place"], "测试起点 → 测试终点")
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
            "title": "挑战单日骑行132公里干到芽庄",
            "place": "绥和 Tuy Hòa",
            "lat": 13.0955,
            "lng": 109.3209,
            "confidence": "Auto-added; pending review",
            "phase": "第二段",
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
            "summary": "测试视频。",
        }
        changed = MODULE.merge_extraction(
            entry,
            {
                "summary": "从绥和骑行到芽庄。",
                "ride": True,
                "distance_km": 132,
                "start_place": "绥和 Tuy Hòa",
                "end_place": "芽庄 Nha Trang",
                "place": "绥和 Tuy Hòa → 芽庄 Nha Trang",
                "foods": [],
                "food_details": [],
                "lodgings": [],
                "costs": [],
                "highlights": [{"seconds": 60, "text": "出发去芽庄"}],
                "confidence_notes": "",
            },
            "2026-07-05T00:00:00+00:00",
        )
        self.assertTrue(changed)
        self.assertEqual(entry["place"], "绥和 Tuy Hòa → 芽庄 Nha Trang")
        self.assertAlmostEqual(entry["lat"], 12.2388)
        self.assertAlmostEqual(entry["lng"], 109.1967)
        self.assertEqual(entry["coordinateSource"], "local-gazetteer")
        self.assertEqual(entry["phase"], "第二段")
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
                "title": "测试",
                "place": "前一站",
                "distanceKm": None,
            },
            [{"start": 15, "end": 20, "text": "从测试起点出发。"}],
        )
        self.assertEqual(parsed["distance_km"], 42)
        self.assertEqual(parsed["highlights"][0]["seconds"], 15)

    def test_manual_entry_is_never_overwritten(self):
        entry = {
            "bvid": "BVKEEP",
            "phase": "第二段",
            "confidence": "视频确认",
            "summary": "人工内容",
            "evidence": [],
        }
        changed = MODULE.merge_extraction(
            entry,
            {"summary": "AI 内容", "highlights": []},
            "2026-07-05T00:00:00+00:00",
        )
        self.assertFalse(changed)
        self.assertEqual(entry["summary"], "人工内容")

    def test_mui_ne_is_available_to_coordinate_lookup(self):
        coordinate = MODULE.coordinate_from_text("骑行到达越南美奈")
        self.assertEqual(coordinate["place"], "美奈 Mũi Né")
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
                "phase": "第二段",
                "ride": True,
                "lat": 11.18,
                "lng": 108.72,
                "distanceKm": 20,
                "highlights": [{}],
                "lodgings": [{}],
                "confidence": "AI音频提取",
            },
            {
                "phase": "第二段",
                "ride": True,
                "lat": 11.18,
                "lng": 108.72,
                "distanceKm": 60,
                "highlights": [{}],
                "lodgings": [{}],
                "confidence": "AI音频提取",
            },
        ]
        MODULE.rebuild_quality_flags(entries)
        self.assertFalse(entries[1]["mapVisible"])
        self.assertIn("坐标与里程冲突", entries[1]["riskFlags"])

    def test_non_ride_day_does_not_replace_previous_route_node(self):
        entries = [
            {
                "phase": "第二段",
                "ride": True,
                "lat": 15.0,
                "lng": 108.0,
                "distanceKm": 40,
                "highlights": [{}],
                "lodgings": [{}],
                "confidence": "AI音频提取",
            },
            {
                "phase": "第二段",
                "ride": False,
                "lat": 13.0,
                "lng": 109.0,
                "distanceKm": 0,
                "highlights": [{}],
                "lodgings": [],
                "confidence": "AI音频提取",
            },
            {
                "phase": "第二段",
                "ride": True,
                "lat": 13.1,
                "lng": 109.1,
                "distanceKm": 40,
                "highlights": [{}],
                "lodgings": [{}],
                "confidence": "AI音频提取",
            },
        ]
        MODULE.rebuild_quality_flags(entries)
        self.assertFalse(entries[2]["mapVisible"])
        self.assertIn("坐标与里程冲突", entries[2]["riskFlags"])


if __name__ == "__main__":
    unittest.main()
