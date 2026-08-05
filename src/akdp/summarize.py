"""Incremental LLM summarization for story chapters and events.

Two levels (matching the existing PRTS-MCP data contract):
  summaries.json       — per-chapter, keyed by storyTxt path
  event_summaries.json — per-event, keyed by event id

Incremental: the candidate tree carries forward summaries.json and
event_summaries.json from the baseline merge. Only chapters/events NOT
already in those files trigger LLM calls. A typical game update costs
~10-20 chapter calls + 1-3 event calls, vs ~2500 for a full rebuild.

API: any OpenAI Chat Completions compatible endpoint, configured via
  LLM_BASE_URL  (default: https://api.openai.com/v1)
  LLM_API_KEY   (required when work exists)
  LLM_MODEL     (default: gpt-4o-mini)

Prompts and text extraction are ported verbatim from the original
ArknightsStoryJson/scripts/summarize.py to preserve stylistic consistency
with the 1993 pre-existing summaries.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "8"))
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds

# ---------------------------------------------------------------------------
# Prompts (verbatim from original to preserve summary style)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = "你是明日方舟官方剧情编辑，擅长将游戏对话提炼为精炼的叙事摘要。"

CHAPTER_PROMPT = """请将以下章节对话总结为一段连贯的中文摘要（5~7句话），保留关键情节转折、角色互动和情感变化。不要逐句翻译，要提炼核心叙事。

章节：{code} {name}
活动：{event_name}
标签：{tag}

{text}

摘要："""

EVENT_PROMPT = """请将以下明日方舟活动「{event_name}」的完整剧情对话总结为一段300~500字的中文梗概。

要求：
- 覆盖主线脉络和核心冲突
- 突出关键角色的动机转变和重要抉择
- 捕捉章节之间的因果联系和伏笔照应
- 写出结局的情感和主题落点
- 用自然流畅的叙事语言，不要机械罗列章节

{full_text}

「{event_name}」梗概："""

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class SummarizeStats:
    chapters_total: int = 0
    chapters_reused: int = 0
    chapters_generated: int = 0
    chapters_failed: int = 0
    events_total: int = 0
    events_reused: int = 0
    events_generated: int = 0
    events_failed: int = 0
    api_calls: int = 0
    failed_details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


# ---------------------------------------------------------------------------
# Text extraction (verbatim logic from original)
# ---------------------------------------------------------------------------

_RICH_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    text = text.replace("{@nickname}", "博士")
    text = _RICH_TAG_RE.sub("", text)
    return text.strip()


def extract_chapter_text(raw: dict) -> str:
    """Extract readable dialogue from a story chapter JSON."""
    lines: list[str] = []
    for item in raw.get("storyList") or []:
        prop = (item.get("prop") or "").lower()
        attrs = item.get("attributes") or {}

        if prop == "name":
            name = attrs.get("name") or ""
            content = attrs.get("content") or ""
            if content:
                role = _clean(str(name)) if name else "？？？"
                lines.append(f"{role}：{_clean(str(content))}")
        elif prop in ("sticker", "subtitle", "animtext"):
            content = attrs.get("content") or attrs.get("text") or ""
            if content:
                lines.append(f"*{_clean(str(content))}*")
        elif prop == "decision":
            options = attrs.get("options") or []
            for opt in options:
                text = opt if isinstance(opt, str) else (opt.get("text") or "" if isinstance(opt, dict) else "")
                if text:
                    lines.append(f"【选项】{_clean(str(text))}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API call (generic OpenAI Chat Completions compatible)
# ---------------------------------------------------------------------------


def _call_api(messages: list[dict], max_tokens: int) -> str:
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY not set")

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
    )

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = exc.read().decode(errors="replace")
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * (2 ** attempt)
                print(f"  HTTP {exc.code}, retrying in {wait}s... ({detail[:200]})")
                time.sleep(wait)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * (2 ** attempt)
                print(f"  {exc}, retrying in {wait}s...")
                time.sleep(wait)

    raise RuntimeError(f"API call failed after {MAX_RETRIES + 1} attempts: {last_error}")


def summarize_chapter(code: str, name: str, event_name: str, tag: str, text: str) -> str:
    if not text.strip():
        return "（无对话内容）"
    prompt = CHAPTER_PROMPT.format(
        code=code, name=name, event_name=event_name, tag=tag or "无", text=text,
    )
    return _call_api(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=600,
    )


def summarize_event(event_name: str, full_text: str) -> str:
    prompt = EVENT_PROMPT.format(event_name=event_name, full_text=full_text)
    return _call_api(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=1200,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

STORY_REVIEW_TABLE = "gamedata/excel/story_review_table.json"
STORY_DIR = "gamedata/story"


def _iter_chapters(zh: Path) -> list[dict]:
    """Discover all story chapters from story_review_table."""
    review_path = zh / STORY_REVIEW_TABLE
    if not review_path.is_file():
        return []
    review = json.loads(review_path.read_text(encoding="utf-8"))
    chapters: list[dict] = []
    for ev_id, entry in review.items():
        if not isinstance(entry, dict):
            continue
        event_name = entry.get("name") or ev_id
        for d in sorted(entry.get("infoUnlockDatas") or [], key=lambda x: x.get("storySort", 0)):
            story_key = d.get("storyTxt")
            if not story_key:
                continue
            story_path = zh / STORY_DIR / f"{story_key}.json"
            if not story_path.is_file():
                continue
            chapters.append({
                "story_key": story_key,
                "story_path": str(story_path),
                "code": d.get("storyCode", ""),
                "name": d.get("storyName", ""),
                "tag": d.get("avgTag") or "",
                "event_id": ev_id,
                "event_name": event_name,
            })
    return chapters


def run_summarize(candidate: Path) -> SummarizeStats:
    """Generate missing chapter and event summaries incrementally.

    Reads existing summaries from the candidate tree (carried by merge),
    only calls the LLM for chapters/events not yet covered.
    """
    zh = candidate / "zh_CN"
    stats = SummarizeStats()

    chapters = _iter_chapters(zh)
    stats.chapters_total = len(chapters)
    if not chapters:
        return stats

    # Load existing summaries (the "cache" — carried by cumulative merge)
    summaries_path = zh / "summaries.json"
    event_summaries_path = zh / "event_summaries.json"
    chapter_summaries: dict[str, str] = {}
    event_summaries: dict[str, str] = {}
    if summaries_path.exists():
        chapter_summaries = json.loads(summaries_path.read_text(encoding="utf-8"))
    if event_summaries_path.exists():
        event_summaries = json.loads(event_summaries_path.read_text(encoding="utf-8"))

    # --- Phase 1: chapter summaries (incremental) ---
    current_keys = {ch["story_key"] for ch in chapters}
    chapter_summaries = {k: v for k, v in chapter_summaries.items() if k in current_keys}
    pending = [ch for ch in chapters if ch["story_key"] not in chapter_summaries]
    stats.chapters_reused = len(chapters) - len(pending)

    # Collect full texts for dirty events (needed regardless of API key)
    chapter_texts: dict[str, str] = {}

    if pending and not LLM_API_KEY:
        print(f"[summarize] WARNING: {len(pending)} chapters need summaries but LLM_API_KEY not set; "
              f"shipping partial cache only")
        stats.chapters_failed = len(pending)
    elif pending:
        print(f"[summarize] chapters: reuse={stats.chapters_reused} generate={len(pending)}")

        def _do_chapter(ch: dict) -> tuple[str, str, str]:
            raw = json.loads(Path(ch["story_path"]).read_text(encoding="utf-8"))
            text = extract_chapter_text(raw)
            summary = summarize_chapter(ch["code"], ch["name"], ch["event_name"], ch["tag"], text)
            return ch["story_key"], summary, text

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            futures = {pool.submit(_do_chapter, ch): ch for ch in pending}
            for future in as_completed(futures):
                ch = futures[future]
                try:
                    key, summary, text = future.result()
                    chapter_summaries[key] = summary
                    chapter_texts[key] = text
                    stats.chapters_generated += 1
                    stats.api_calls += 1
                    print(f"  [{stats.chapters_generated}/{len(pending)}] {ch['code']} {ch['name']} ({len(summary)} chars)")
                except Exception as exc:
                    stats.chapters_failed += 1
                    stats.failed_details.append({"type": "chapter", "key": ch["story_key"], "error": str(exc)})
                    print(f"  FAILED {ch['code']} {ch['name']}: {exc}", flush=True)

    # --- Phase 2: event summaries (incremental, dirty events only) ---
    from collections import Counter

    chapter_counts = Counter(ch["event_id"] for ch in chapters if ch["event_id"])
    stats.events_total = len(chapter_counts)

    # Dirty = has new chapters OR missing event summary
    pending_keys = {ch["story_key"] for ch in pending}
    dirty_events = set()
    for ch in chapters:
        ev = ch["event_id"]
        if not ev:
            continue
        if ch["story_key"] in pending_keys or ev not in event_summaries:
            dirty_events.add(ev)

    stats.events_reused = stats.events_total - len(dirty_events)

    if dirty_events and not LLM_API_KEY:
        print(f"[summarize] WARNING: {len(dirty_events)} events need summaries but LLM_API_KEY not set")
        stats.events_failed = len(dirty_events)
    elif dirty_events:
        print(f"[summarize] events: reuse={stats.events_reused} generate={len(dirty_events)}")

        # Build per-event chapter groupings
        key_to_ch = {ch["story_key"]: ch for ch in chapters}
        events: dict[str, dict] = {}
        for ch in chapters:
            ev = ch["event_id"]
            if not ev or ev not in dirty_events:
                continue
            if ev not in events:
                events[ev] = {"event_name": ch["event_name"], "story_keys": [], "parts": []}
            events[ev]["story_keys"].append(ch["story_key"])

        # Ensure chapter text for all dirty-event chapters
        for ev_id, ev_data in events.items():
            for sk in ev_data["story_keys"]:
                if sk not in chapter_texts:
                    ch = key_to_ch.get(sk)
                    if ch:
                        try:
                            raw = json.loads(Path(ch["story_path"]).read_text(encoding="utf-8"))
                            chapter_texts[sk] = extract_chapter_text(raw)
                        except Exception:
                            chapter_texts[sk] = ""

            ev_data["parts"] = []
            for sk in ev_data["story_keys"]:
                ch = key_to_ch[sk]
                text = chapter_texts.get(sk, "")
                if not text:
                    continue
                header = f"--- {ch['code']}"
                if ch.get("tag"):
                    header += f" [{ch['tag']}]"
                header += f" {ch['name']} ---"
                ev_data["parts"].append(f"{header}\n{text}")

        def _do_event(ev_id: str, ev_data: dict) -> tuple[str, str]:
            parts = ev_data["parts"]
            if len(parts) <= 1:
                # Single-chapter events: truncate the chapter text, no API call
                text = parts[0].split("\n", 1)[-1] if parts else ""
                return ev_id, text[:800]
            full_text = "\n\n".join(parts)
            return ev_id, summarize_event(ev_data["event_name"], full_text)

        sorted_events = sorted(events.items(), key=lambda x: len(x[1]["parts"]), reverse=True)
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            futures = {pool.submit(_do_event, eid, edata): eid for eid, edata in sorted_events}
            for future in as_completed(futures):
                ev_id = futures[future]
                ev_data = events[ev_id]
                try:
                    result_id, summary = future.result()
                    event_summaries[result_id] = summary
                    # Only count as generated if it wasn't a single-chapter truncation
                    if len(ev_data["parts"]) > 1:
                        stats.events_generated += 1
                        stats.api_calls += 1
                    else:
                        stats.events_reused += 1
                    print(f"  event {ev_data['event_name']}: {len(ev_data['parts'])} chapters → {len(summary)} chars")
                except Exception as exc:
                    stats.events_failed += 1
                    stats.failed_details.append({"type": "event", "key": ev_id, "error": str(exc)})
                    print(f"  FAILED event {ev_data['event_name']}: {exc}", flush=True)

    # --- Write results ---
    summaries_path.parent.mkdir(parents=True, exist_ok=True)
    summaries_path.write_text(
        json.dumps(chapter_summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    event_summaries_path.write_text(
        json.dumps(event_summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"[summarize] done: chapters +{stats.chapters_generated}/{stats.chapters_failed}fail, "
          f"events +{stats.events_generated}/{stats.events_failed}fail, "
          f"api_calls={stats.api_calls}")
    return stats
