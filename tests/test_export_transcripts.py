import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ENRICH_SPEC = importlib.util.spec_from_file_location(
    "enrich_latest_video", ROOT / "scripts" / "enrich_latest_video.py"
)
ENRICH = importlib.util.module_from_spec(ENRICH_SPEC)
assert ENRICH_SPEC.loader is not None
ENRICH_SPEC.loader.exec_module(ENRICH)

EXPORT_SPEC = importlib.util.spec_from_file_location(
    "export_transcripts", ROOT / "scripts" / "export_transcripts.py"
)
EXPORT = importlib.util.module_from_spec(EXPORT_SPEC)
assert EXPORT_SPEC.loader is not None
EXPORT_SPEC.loader.exec_module(EXPORT)


class TranscriptExportTests(unittest.TestCase):
    def test_vtt_timestamp(self):
        self.assertEqual(EXPORT.vtt_timestamp(65.125), "00:01:05.125")

    def test_export_cache_creates_json_text_and_vtt(self):
        payload = {
            "schemaVersion": 1,
            "bvid": "BVTEST789",
            "title": "测试视频",
            "segments": [
                {"start": 65.125, "end": 68.5, "text": "今天骑了四十公里。"}
            ],
        }
        secret = "export-test-key"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "BVTEST789.json.enc"
            cache.write_bytes(ENRICH.encrypt_transcript_payload(payload, secret))
            paths = EXPORT.export_cache(cache, root / "output", secret)
            contents = {path.suffix: path.read_text(encoding="utf-8") for path in paths}

        self.assertEqual({path.suffix for path in paths}, {".json", ".txt", ".vtt"})
        self.assertIn("[01:05] 今天骑了四十公里。", contents[".txt"])
        self.assertIn("00:01:05.125 --> 00:01:08.500", contents[".vtt"])
        self.assertEqual(json.loads(contents[".json"])["bvid"], "BVTEST789")


if __name__ == "__main__":
    unittest.main()
