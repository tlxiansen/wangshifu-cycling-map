#!/usr/bin/env python3
"""Enrich the newest auto-added Bilibili episode from its audio.

Raw audio and full transcripts live only in a temporary directory. The script
stores structured facts and timestamped evidence in wangshifu-data.json.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = ROOT / "wangshifu-data.json"
VIDEO_URL = "https://www.bilibili.com/video/{bvid}/"
AUTO_PHASES = {"Auto-added", "自动添加"}
AUTO_CONFIDENCE_MARKERS = ("auto-added", "pending review", "待核验", "ai提取")


def log(message: str) -> None:
    print(f"[audio-enrichment] {message}", flush=True)


def append_step_summary(lines: list[str]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def format_timestamp(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def source_link(bvid: str, seconds: int | float | None = None) -> str:
    base = VIDEO_URL.format(bvid=bvid)
    if seconds is None:
        return base
    return f"{base}?t={max(0, int(seconds))}"


def read_json(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a JSON array")
    return value


def write_json(path: Path, value: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def has_audio_evidence(entry: dict[str, Any]) -> bool:
    evidence = entry.get("evidence") or []
    return any(
        str(item.get("type", "")).lower()
        in {"audio-transcript", "ai-audio-transcript"}
        for item in evidence
        if isinstance(item, dict)
    )


def is_auto_managed(entry: dict[str, Any]) -> bool:
    if str(entry.get("phase", "")).strip().lower() in {
        value.lower() for value in AUTO_PHASES
    }:
        return True
    confidence = str(entry.get("confidence", "")).lower()
    return any(marker in confidence for marker in AUTO_CONFIDENCE_MARKERS)


def select_candidates(
    entries: list[dict[str, Any]], lookback: int, maximum: int
) -> list[dict[str, Any]]:
    recent = entries[-max(1, lookback) :]
    candidates = [
        item
        for item in reversed(recent)
        if item.get("bvid") and is_auto_managed(item) and not has_audio_evidence(item)
    ]
    return candidates[: max(1, maximum)]


def cookie_file_from_environment(work_dir: Path) -> Path | None:
    raw = os.getenv("BILIBILI_COOKIES", "").strip()
    encoded = os.getenv("BILIBILI_COOKIES_BASE64", "").strip()
    if not raw and encoded:
        raw = base64.b64decode(encoded).decode("utf-8")
    if not raw:
        return None
    path = work_dir / "bilibili-cookies.txt"
    path.write_text(raw.replace("\r\n", "\n") + "\n", encoding="utf-8")
    return path


def download_audio(entry: dict[str, Any], work_dir: Path) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed") from exc

    cookie_path = cookie_file_from_environment(work_dir)
    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(work_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "Referer": "https://www.bilibili.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
        },
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }
        ],
    }
    if cookie_path:
        options["cookiefile"] = str(cookie_path)

    url = source_link(str(entry["bvid"]))
    log(f"Downloading audio for {entry['bvid']}")
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])

    audio_path = work_dir / "source.mp3"
    if not audio_path.exists():
        matches = list(work_dir.glob("source.*"))
        if not matches:
            raise FileNotFoundError("yt-dlp completed but produced no audio file")
        audio_path = matches[0]
    return audio_path


def split_audio(audio_path: Path, work_dir: Path, chunk_seconds: int) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found")
    chunk_pattern = work_dir / "chunk-%03d.mp3"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(audio_path),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-reset_timestamps",
        "1",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(chunk_pattern),
    ]
    subprocess.run(command, check=True)
    chunks = sorted(work_dir.glob("chunk-*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no audio chunks")
    return chunks


def object_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def transcribe_chunks(
    client: Any, chunks: list[Path], chunk_seconds: int
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    prompt = (
        "这是中国骑行旅行视频，主要使用普通话，涉及越南、马来西亚、新加坡地名，"
        "以及骑行里程、酒店、餐饮、价格。请忠实转写，不要翻译。"
    )
    for index, chunk in enumerate(chunks):
        log(f"Transcribing chunk {index + 1}/{len(chunks)}")
        with open(chunk, "rb") as audio:
            response = client.audio.transcriptions.create(
                model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1"),
                file=audio,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                prompt=prompt,
            )
        offset = index * chunk_seconds
        segments = object_value(response, "segments", []) or []
        if not segments:
            text = str(object_value(response, "text", "")).strip()
            if text:
                result.append({"start": offset, "end": offset + chunk_seconds, "text": text})
            continue
        for segment in segments:
            text = str(object_value(segment, "text", "")).strip()
            if not text:
                continue
            result.append(
                {
                    "start": offset + float(object_value(segment, "start", 0)),
                    "end": offset + float(object_value(segment, "end", 0)),
                    "text": text,
                }
            )
    return result


def transcript_for_model(segments: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{format_timestamp(segment['start'])}] {segment['text']}"
        for segment in segments
    )


def extract_structured_episode(
    client: Any, entry: dict[str, Any], segments: list[dict[str, Any]]
) -> dict[str, Any]:
    try:
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise RuntimeError("pydantic is not installed") from exc

    class Highlight(BaseModel):
        seconds: int = Field(ge=0)
        text: str
        confidence: str

    class FoodDetail(BaseModel):
        name: str
        venue: str | None
        meal: str | None
        price: float | None
        currency: str | None
        price_note: str | None
        recommendation: str | None
        seconds: int | None

    class Lodging(BaseModel):
        name: str | None
        area: str | None
        price: float | None
        currency: str | None
        booking: str | None
        bike_storage: str | None
        pros: str | None
        cons: str | None
        recommendation: str | None
        seconds: int | None

    class Cost(BaseModel):
        category: str
        label: str
        amount: float
        currency: str
        seconds: int | None

    class EpisodeExtraction(BaseModel):
        summary: str
        ride: bool | None
        distance_km: float | None
        ride_time_hours: float | None
        day_time_hours: float | None
        start_place: str | None
        end_place: str | None
        place: str | None
        foods: list[str]
        food_details: list[FoodDetail]
        lodgings: list[Lodging]
        costs: list[Cost]
        highlights: list[Highlight]
        confidence_notes: str

    transcript = transcript_for_model(segments)
    metadata = {
        "date": entry.get("date"),
        "bvid": entry.get("bvid"),
        "title": entry.get("title"),
        "existing_place": entry.get("place"),
        "title_distance_km": entry.get("distanceKm"),
    }
    instructions = """
你负责从骑行旅行视频的带时间戳字幕中提取可验证事实，并输出简体中文。

规则：
1. 只使用标题、元数据和字幕明确出现的信息，不依靠常识猜测。
2. 地名、酒店名、餐厅名、金额、币种和里程不确定时填 null，不要编造。
3. place 优先写成“起点 → 终点”；只有终点时写终点；休整日注明“（休整）”。
4. distance_km 是当天实际骑行距离。计划里程、码表中途读数不得当成最终里程。
5. food_details、lodgings、costs 的 seconds 指该事实首次得到明确支持的时间。
6. highlights 选择 5～12 个对旅行者最有用的事件，包括出发、到达、路线变化、
   景点、住宿、饮食、价格、故障、风险和重要提醒。seconds 必须来自字幕时间戳。
7. summary 用 1～3 句话概括当天做了什么，不写宣传语。
8. confidence_notes 简述仍需人工核验的内容。
"""
    response = client.responses.parse(
        model=os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-5-mini"),
        input=[
            {"role": "system", "content": instructions.strip()},
            {
                "role": "user",
                "content": (
                    "视频元数据：\n"
                    + json.dumps(metadata, ensure_ascii=False, indent=2)
                    + "\n\n带时间戳字幕：\n"
                    + transcript
                ),
            },
        ],
        text_format=EpisodeExtraction,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("The extraction model returned no structured output")
    return parsed.model_dump()


def status_at(seconds: int | float | None) -> str:
    if seconds is None:
        return "AI音频提取，时间点待核验"
    return f"AI音频提取 {format_timestamp(seconds)}"


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def merge_extraction(
    entry: dict[str, Any], extraction: dict[str, Any], processed_at: str
) -> bool:
    """Merge only into an auto-managed episode and never erase existing facts."""
    if not is_auto_managed(entry) or has_audio_evidence(entry):
        return False

    bvid = str(entry["bvid"])
    base_source = source_link(bvid)

    if clean_text(extraction.get("summary")):
        entry["summary"] = clean_text(extraction["summary"])
    if extraction.get("ride") is not None:
        entry["ride"] = bool(extraction["ride"])
    if entry.get("distanceKm") is None and extraction.get("distance_km") is not None:
        entry["distanceKm"] = extraction["distance_km"]
    if entry.get("rideTimeHours") is None and extraction.get("ride_time_hours") is not None:
        entry["rideTimeHours"] = extraction["ride_time_hours"]
    if entry.get("dayTimeHours") is None and extraction.get("day_time_hours") is not None:
        entry["dayTimeHours"] = extraction["day_time_hours"]

    extracted_place = clean_text(extraction.get("place"))
    if extracted_place:
        entry["place"] = extracted_place

    if not entry.get("foods"):
        entry["foods"] = [
            text
            for value in extraction.get("foods", [])
            if (text := clean_text(value))
        ]
    if entry.get("foods") and (
        not clean_text(entry.get("food"))
        or str(entry.get("food")).lower() == "not identified"
    ):
        entry["food"] = "、".join(entry["foods"])

    if not entry.get("foodDetails"):
        entry["foodDetails"] = []
        for item in extraction.get("food_details", []):
            seconds = item.get("seconds")
            name = clean_text(item.get("name"))
            if not name:
                continue
            entry["foodDetails"].append(
                {
                    "name": name,
                    "venue": clean_text(item.get("venue")) or "店名待核验",
                    "meal": clean_text(item.get("meal")),
                    "price": item.get("price"),
                    "currency": clean_text(item.get("currency")),
                    "priceNote": clean_text(item.get("price_note")),
                    "recommendation": clean_text(item.get("recommendation")),
                    "status": status_at(seconds),
                    "source": source_link(bvid, seconds),
                }
            )

    if not entry.get("lodgings"):
        entry["lodgings"] = []
        for item in extraction.get("lodgings", []):
            seconds = item.get("seconds")
            entry["lodgings"].append(
                {
                    "name": clean_text(item.get("name")) or "名称待核验",
                    "area": clean_text(item.get("area")) or "区域待核验",
                    "price": item.get("price"),
                    "currency": clean_text(item.get("currency")),
                    "booking": clean_text(item.get("booking")) or "视频未说明",
                    "bikeStorage": clean_text(item.get("bike_storage")) or "待核验",
                    "pros": clean_text(item.get("pros")),
                    "cons": clean_text(item.get("cons")),
                    "recommendation": clean_text(item.get("recommendation"))
                    or "视频入住",
                    "status": status_at(seconds),
                    "source": source_link(bvid, seconds),
                }
            )

    if not entry.get("costs"):
        entry["costs"] = []
        for item in extraction.get("costs", []):
            amount = item.get("amount")
            currency = clean_text(item.get("currency"))
            if amount is None or not currency:
                continue
            seconds = item.get("seconds")
            entry["costs"].append(
                {
                    "category": clean_text(item.get("category")) or "其他",
                    "label": clean_text(item.get("label")) or "视频口述花费",
                    "amount": amount,
                    "currency": currency,
                    "status": status_at(seconds),
                    "source": source_link(bvid, seconds),
                }
            )

    if not entry.get("highlights"):
        entry["highlights"] = []
        seen_seconds: set[int] = set()
        for item in extraction.get("highlights", []):
            seconds = max(0, int(item.get("seconds") or 0))
            text = clean_text(item.get("text"))
            if not text or seconds in seen_seconds:
                continue
            seen_seconds.add(seconds)
            entry["highlights"].append(
                {
                    "time": format_timestamp(seconds),
                    "text": text,
                    "status": "AI音频提取，待人工核验",
                    "source": source_link(bvid, seconds),
                }
            )

    evidence = list(entry.get("evidence") or [])
    evidence.append(
        {
            "type": "ai-audio-transcript",
            "url": base_source,
            "note": (
                "音频自动转写并结构化提取；未保存原始音频和完整字幕。"
                + (clean_text(extraction.get("confidence_notes")) or "")
            ),
        }
    )
    entry["evidence"] = evidence
    entry["confidence"] = "AI音频提取，待维护者核验"
    entry["phase"] = "AI enriched"
    entry["aiEnrichment"] = {
        "processedAt": processed_at,
        "transcriptionModel": os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1"),
        "extractionModel": os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-5-mini"),
        "status": "待核验",
    }
    return True


def process_entry(entry: dict[str, Any], chunk_seconds: int) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai is not installed") from exc

    client = OpenAI()
    with tempfile.TemporaryDirectory(prefix="wangshifu-audio-") as temporary:
        work_dir = Path(temporary)
        audio_path = download_audio(entry, work_dir)
        chunks = split_audio(audio_path, work_dir, chunk_seconds)
        segments = transcribe_chunks(client, chunks, chunk_seconds)
        if not segments:
            raise RuntimeError("Transcription returned no timestamped text")
        return extract_structured_episode(client, entry, segments)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument(
        "--lookback",
        type=int,
        default=int(os.getenv("AI_LOOKBACK_EPISODES", "3")),
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=int(os.getenv("MAX_AI_EPISODES", "1")),
    )
    parser.add_argument(
        "--chunk-seconds",
        type=int,
        default=int(os.getenv("AUDIO_CHUNK_SECONDS", "600")),
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Merge a local extraction JSON fixture without network access.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = read_json(args.data)
    candidates = select_candidates(entries, args.lookback, args.max_episodes)
    if not candidates:
        log("No new auto-managed episode needs audio enrichment.")
        append_step_summary(["### 音频信息提取", "- 没有需要处理的新视频。"])
        return 0

    if not args.fixture and not os.getenv("OPENAI_API_KEY", "").strip():
        log("OPENAI_API_KEY is not configured; skipping safely.")
        append_step_summary(
            [
                "### 音频信息提取",
                "- 已发现新视频，但仓库尚未配置 `OPENAI_API_KEY`，本次安全跳过。",
            ]
        )
        return 0

    changed: list[str] = []
    processed_at = datetime.now(timezone.utc).isoformat()
    for entry in candidates:
        bvid = str(entry["bvid"])
        log(f"Processing {entry.get('date', '')} {bvid} {entry.get('title', '')}")
        if args.fixture:
            with open(args.fixture, "r", encoding="utf-8-sig") as handle:
                extraction = json.load(handle)
        else:
            extraction = process_entry(entry, args.chunk_seconds)
        if merge_extraction(entry, extraction, processed_at):
            changed.append(bvid)

    if changed:
        write_json(args.data, entries)
        log(f"Updated {args.data}: {', '.join(changed)}")
        append_step_summary(
            [
                "### 音频信息提取",
                f"- 已处理：`{', '.join(changed)}`",
                "- 原始音频和完整字幕已删除，只提交结构化事实与时间点。",
                "- 状态：AI 提取，待维护者核验。",
            ]
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        log(f"ERROR: {error}")
        append_step_summary(
            ["### 音频信息提取", f"- 失败：`{type(error).__name__}: {error}`"]
        )
        raise
