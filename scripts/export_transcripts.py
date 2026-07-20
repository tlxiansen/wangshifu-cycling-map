#!/usr/bin/env python3
"""Decrypt cached transcripts and export JSON, timestamped TXT, and WebVTT."""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from enrich_latest_video import decrypt_transcript_payload, format_timestamp


def vtt_timestamp(seconds: int | float | None) -> str:
    milliseconds = max(0, round(float(seconds or 0) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def cleaned_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Transcript cache contains no segments")
    result: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(segment.get("start") or 0))
        end = max(start, float(segment.get("end") or start))
        result.append({"start": start, "end": end, "text": text})
    if not result:
        raise RuntimeError("Transcript cache contains no usable segments")
    return result


def export_cache(path: Path, output_dir: Path, secret: str) -> list[Path]:
    payload = decrypt_transcript_payload(path.read_bytes(), secret)
    segments = cleaned_segments(payload)
    bvid = str(payload.get("bvid") or path.name.split(".", 1)[0]).strip()
    if not bvid or not all(character.isalnum() for character in bvid):
        raise RuntimeError(f"Transcript cache has an invalid BV id: {path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{bvid}.json"
    text_path = output_dir / f"{bvid}.txt"
    vtt_path = output_dir / f"{bvid}.vtt"

    export_payload = dict(payload)
    export_payload["segments"] = segments
    json_path.write_text(
        json.dumps(export_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(
        "\n".join(
            f"[{format_timestamp(segment['start'])}] {segment['text']}"
            for segment in segments
        )
        + "\n",
        encoding="utf-8",
    )
    vtt_lines = ["WEBVTT", ""]
    for index, segment in enumerate(segments, start=1):
        vtt_lines.extend(
            [
                str(index),
                f"{vtt_timestamp(segment['start'])} --> {vtt_timestamp(segment['end'])}",
                segment["text"],
                "",
            ]
        )
    vtt_path.write_text("\n".join(vtt_lines), encoding="utf-8")
    return [json_path, text_path, vtt_path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("transcripts"))
    parser.add_argument("--output-dir", type=Path, default=Path("exported-transcripts"))
    parser.add_argument("--bvid", help="Only export one BV id; default exports every cache")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    secret = os.getenv("TRANSCRIPT_ENCRYPTION_KEY", "").strip()
    if not secret:
        secret = getpass.getpass("Transcript encryption key: ").strip()
    if not secret:
        raise RuntimeError("TRANSCRIPT_ENCRYPTION_KEY is required")

    if args.bvid:
        if not all(character.isalnum() for character in args.bvid):
            raise ValueError("--bvid may only contain letters and numbers")
        paths = [args.input_dir / f"{args.bvid}.json.enc"]
    else:
        paths = sorted(args.input_dir.glob("*.json.enc"))
    if not paths:
        raise FileNotFoundError("No encrypted transcript caches were found")

    exported: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        exported.extend(export_cache(path, args.output_dir, secret))
    print(f"Exported {len(exported)} files to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
