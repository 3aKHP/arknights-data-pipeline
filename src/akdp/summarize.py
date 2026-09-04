"""Incremental LLM summarization for story chapters and events.

Two levels (matching the existing PRTS-MCP data contract):
  summaries.json       — per-chapter, keyed by storyTxt path
  event_summaries.json — per-event, keyed by event id

Incremental: the candidate tree carries forward summaries.json and
event_summaries.json from the baseline merge. Only chapters/events NOT
already in those files trigger LLM calls. A typical game update costs
~10-20 chapter calls + 1-3 event calls, vs ~2500 for a full rebuild.

Every response must pass the shared acceptance gate (summary_gate) before
it is written: rejected responses are regenerated in-process (bounded) and
finally reported through failed_details so the pipeline fails closed. Each
attempt is recorded in a per-run call ledger (work/llm-ledger.jsonl) with
full provider detail; CI ships that ledger to a private endpoint, it never
enters any public artifact. Sidecar files summaries.meta.json /
event_summaries.meta.json record acceptance metadata per key and ship next
to the data files inside the story zip.

API: any OpenAI Chat Completions compatible endpoint, configured via
  LLM_BASE_URL  (default: https://api.openai.com/v1)
  LLM_API_KEY   (required when work exists)
  LLM_MODEL     (default: gpt-4o-mini)

Prompts and text extraction are kept compatible with the pre-existing summaries;
the runtime does not fetch or depend on any legacy data repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .summary_gate import (
    CHAPTER,
    EVENT,
    SENTINEL_NO_DIALOGUE,
    accept_summary,
    classify_finish_reason,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "8"))


def _load_extra_body() -> dict:
    """Provider-specific request extensions, e.g. DeepSeek's
    ``{"thinking": {"type": "none"}}`` to disable reasoning. Parsed once at
    import; invalid JSON degrades to {} with a warning instead of killing
    the pipeline."""
    raw = os.environ.get("LLM_EXTRA_BODY", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[summarize] WARNING: LLM_EXTRA_BODY is not valid JSON; ignoring: {raw[:100]}")
        return {}
    if not isinstance(parsed, dict):
        print("[summarize] WARNING: LLM_EXTRA_BODY must be a JSON object; ignoring")
        return {}
    return parsed


LLM_EXTRA_BODY = _load_extra_body()

#: 429/5xx/network errors are retried with full-jitter exponential backoff;
#: other 4xx are deterministic failures and abort immediately.
MAX_TRANSPORT_RETRIES = 3
#: rejected content and transient finish_reason values are regenerated at
#: most this many times before the call lands in failed_details
CONTENT_REGEN_ATTEMPTS = 2
RETRY_DELAY_BASE = 2.0  # seconds; full-jitter exponential backoff

PROMPT_VERSION = "story-summaries/v1"
GATE_VERSION = "summary-gate/v1"
INPUT_HASH_DOMAIN = "akdp:summarize-input:v1:"

#: Output budgets must cover invisible reasoning tokens too: on
#: reasoning-capable models (deepseek-v4-flash) thinking counts against
#: max_tokens, and a tight cap surfaces as finish_reason=length with EMPTY
#: content — the exact signature of the 2026-09-03 incident (5 empty chapter
#: summaries + a truncated event summary). These caps leave reasoning ample
#: headroom; the acceptance gate still fails closed if a cap is ever hit.
CHAPTER_MAX_TOKENS = 4096
EVENT_MAX_TOKENS = 8192

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
# Small helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _sleep_jitter(step: int) -> None:
    """Full-jitter exponential backoff; step counts from 1."""
    time.sleep(random.uniform(0, RETRY_DELAY_BASE * (2 ** (step - 1))))


# ---------------------------------------------------------------------------
# API call (generic OpenAI Chat Completions compatible)
# ---------------------------------------------------------------------------


class _TransportError(RuntimeError):
    """HTTP or network level failure, classified for retry decisions."""

    def __init__(self, message: str, *, status: int | None):
        super().__init__(message)
        self.status = status
        self.retryable = status is None or status == 429 or status >= 500


@dataclass
class _CallResult:
    content: str
    finish_reason: str | None
    model: str | None
    response_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


def _request_body(messages: list[dict], max_tokens: int) -> dict:
    """Chat Completions payload; LLM_EXTRA_BODY merges provider extensions."""
    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    body.update(LLM_EXTRA_BODY)
    return body


def _chat_once(messages: list[dict], max_tokens: int) -> _CallResult:
    """One HTTP call, no retry. Raises _TransportError on failure."""
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY not set")

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    body = json.dumps(_request_body(messages, max_tokens)).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors="replace")
        except Exception:  # noqa: BLE001 — best-effort error detail
            detail = ""
        raise _TransportError(f"HTTP {exc.code}: {detail[:200]}", status=exc.code) from exc
    except Exception as exc:  # noqa: BLE001 — timeouts, DNS, bad payload framing
        raise _TransportError(str(exc), status=None) from exc
    latency_ms = int((time.monotonic() - started) * 1000)

    choices = payload.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    usage = payload.get("usage") or {}
    return _CallResult(
        content=str(message.get("content") or "").strip(),
        finish_reason=choice.get("finish_reason"),
        model=payload.get("model"),
        response_id=payload.get("id"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=latency_ms,
    )


class _Ledger:
    """Append-only per-attempt call ledger with full provider detail.

    The ledger is private evidence: it carries the real endpoint and model
    names and is POSTed to a private endpoint by CI. It must never be
    uploaded as a public artifact or embedded in the release manifest —
    the manifest only receives the sanitized digest computed at package
    time. Prompts, responses and story text are never recorded.
    """

    def __init__(self, path: Path, run_meta: dict | None):
        self.path = path
        self.run_meta = dict(run_meta or {})
        self._lock = threading.Lock()

    def write(self, **record: object) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def _generate(
    messages: list[dict],
    *,
    max_tokens: int,
    level: str,
    call_meta: dict,
    ledger: _Ledger,
) -> tuple[str, dict]:
    """One logical call through transport retries, gate checks, re-rolls.

    Returns (content, sidecar_info). Raises RuntimeError once budgets are
    exhausted; the caller turns that into failed_details.
    """
    logical_id = uuid.uuid4().hex[:12]
    input_text = messages[-1]["content"]
    input_hash = _sha256_hex(INPUT_HASH_DOMAIN + input_text)
    run = ledger.run_meta
    transport_attempts = 0
    regen_attempts = 0
    attempt = 0
    retry_reason: str | None = None

    while True:
        attempt += 1
        common: dict = {
            "ts": _utcnow(),
            "run_id": run.get("run_id"),
            "job_id": run.get("job_id"),
            "version_id": run.get("version_id"),
            "level": level,
            "event_id": call_meta.get("event_id"),
            "chapter_key": call_meta.get("chapter_key"),
            "input_sha256": input_hash,
            "input_chars": len(input_text),
            "requested_model": LLM_MODEL,
            "endpoint": LLM_BASE_URL,
            "endpoint_fingerprint": _fingerprint(LLM_BASE_URL),
            "provider": "openai-chat-completions",
            "logical_call_id": logical_id,
            "attempt": attempt,
            "retry_reason": retry_reason,
            "prompt_version": PROMPT_VERSION,
            "gate_version": GATE_VERSION,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "extra_body_keys": sorted(LLM_EXTRA_BODY),
        }
        try:
            result = _chat_once(messages, max_tokens)
        except _TransportError as exc:
            reason = f"transport:{exc.status if exc.status is not None else 'network'}"
            ledger.write(
                **common,
                http_status=exc.status,
                latency_ms=None,
                finish_reason=None,
                actual_model=None,
                model_fingerprint=None,
                prompt_tokens=None,
                completion_tokens=None,
                output_chars=0,
                output_sha256=None,
                verdict="error",
                reject_reason=reason,
            )
            if exc.retryable and transport_attempts < MAX_TRANSPORT_RETRIES:
                transport_attempts += 1
                retry_reason = reason
                print(f"  {reason}, retrying with jittered backoff "
                      f"({transport_attempts}/{MAX_TRANSPORT_RETRIES})...")
                _sleep_jitter(transport_attempts)
                continue
            raise RuntimeError(f"LLM call failed after {attempt} attempts: {exc}") from exc

        if classify_finish_reason(result.finish_reason) == "accept":
            ok, reason_tuple = accept_summary(level, result.content)
            reject_reason = None if ok else reason_tuple
        else:
            ok = False
            reject_reason = f"finish_reason:{result.finish_reason}"
        model_fingerprint = _fingerprint(result.model or LLM_MODEL)
        ledger.write(
            **common,
            http_status=200,
            latency_ms=result.latency_ms,
            finish_reason=result.finish_reason,
            actual_model=result.model,
            model_fingerprint=model_fingerprint,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            output_chars=len(result.content),
            output_sha256=_sha256_hex(result.content) if result.content else None,
            verdict="pass" if ok else "reject",
            reject_reason=reject_reason,
        )
        if ok:
            return result.content, {
                "finish_reason": result.finish_reason,
                "model_fingerprint": model_fingerprint,
            }
        if (classify_finish_reason(result.finish_reason) != "fail"
                and regen_attempts < CONTENT_REGEN_ATTEMPTS):
            regen_attempts += 1
            retry_reason = reject_reason
            print(f"  {level} summary rejected ({reject_reason}), "
                  f"regenerating ({regen_attempts}/{CONTENT_REGEN_ATTEMPTS})...")
            _sleep_jitter(regen_attempts)
            continue
        raise RuntimeError(f"{level} summary rejected: {reject_reason}")


def _summarize_chapter(ch: dict, text: str, ledger: _Ledger) -> tuple[str, dict]:
    """Generate one chapter summary; returns (summary, sidecar_info)."""
    if not text.strip():
        return SENTINEL_NO_DIALOGUE, {"path": "sentinel"}
    prompt = CHAPTER_PROMPT.format(
        code=ch["code"], name=ch["name"], event_name=ch["event_name"], tag=ch["tag"] or "无", text=text,
    )
    content, info = _generate(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=CHAPTER_MAX_TOKENS,
        level=CHAPTER,
        call_meta={"chapter_key": ch["story_key"], "event_id": ch["event_id"]},
        ledger=ledger,
    )
    return content, {"path": "llm", **info}


def _summarize_event(
    event_name: str, full_text: str, event_id: str, ledger: _Ledger,
) -> tuple[str, dict]:
    """Generate one event-level summary from the full chapter texts."""
    if not full_text.strip():
        return SENTINEL_NO_DIALOGUE, {"path": "sentinel"}
    prompt = EVENT_PROMPT.format(event_name=event_name, full_text=full_text)
    content, info = _generate(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=EVENT_MAX_TOKENS,
        level=EVENT,
        call_meta={"event_id": event_id},
        ledger=ledger,
    )
    return content, {"path": "llm", **info}


def _meta_entry(info: dict, content: str) -> dict:
    """Sidecar metadata for one summary key (public, fingerprint-only)."""
    entry: dict = {
        "path": info["path"],
        "prompt_version": PROMPT_VERSION,
        "accepted_at": _utcnow(),
    }
    if info["path"] == "llm":
        entry["output_sha256"] = _sha256_hex(content)
        entry["finish_reason"] = info.get("finish_reason")
        entry["model_fingerprint"] = info.get("model_fingerprint")
        entry["gate_version"] = GATE_VERSION
    return entry


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

STORY_REVIEW_TABLE = "gamedata/excel/story_review_table.json"
STORY_DIR = "gamedata/story"

SIDECAR_FILES = {
    "summaries.meta.json": "chapter",
    "event_summaries.meta.json": "event",
}


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


def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def run_summarize(candidate: Path, run_meta: dict | None = None) -> SummarizeStats:
    """Generate missing chapter and event summaries incrementally.

    Reads existing summaries from the candidate tree (carried by merge),
    only calls the LLM for chapters/events not yet covered.
    """
    zh = candidate / "zh_CN"
    stats = SummarizeStats()

    meta = dict(run_meta or {})
    meta.setdefault("run_id", os.environ.get("GITHUB_RUN_ID"))
    meta.setdefault("job_id", os.environ.get("GITHUB_JOB"))
    ledger = _Ledger(candidate.parent / "llm-ledger.jsonl", meta)

    chapters = _iter_chapters(zh)
    stats.chapters_total = len(chapters)
    if not chapters:
        return stats

    # Load existing summaries (the "cache" — carried by cumulative merge)
    summaries_path = zh / "summaries.json"
    event_summaries_path = zh / "event_summaries.json"
    chapter_summaries: dict[str, str] = _load_json(summaries_path, {})
    event_summaries: dict[str, str] = _load_json(event_summaries_path, {})
    chapter_meta: dict[str, dict] = _load_json(zh / "summaries.meta.json", {})
    event_meta: dict[str, dict] = _load_json(zh / "event_summaries.meta.json", {})

    # --- Phase 1: chapter summaries (incremental) ---
    current_keys = {ch["story_key"] for ch in chapters}
    chapter_summaries = {k: v for k, v in chapter_summaries.items() if k in current_keys}
    pending = [ch for ch in chapters if ch["story_key"] not in chapter_summaries]
    stats.chapters_reused = len(chapters) - len(pending)

    # Collect full texts for dirty events (needed regardless of api key)
    chapter_texts: dict[str, str] = {}

    if pending and not LLM_API_KEY:
        print(f"[summarize] WARNING: {len(pending)} chapters need summaries but LLM_API_KEY not set; "
              f"shipping partial cache only")
        stats.chapters_failed = len(pending)
    elif pending:
        print(f"[summarize] chapters: reuse={stats.chapters_reused} generate={len(pending)}")

        def _do_chapter(ch: dict) -> tuple[str, str, str, dict]:
            raw = json.loads(Path(ch["story_path"]).read_text(encoding="utf-8"))
            text = extract_chapter_text(raw)
            summary, info = _summarize_chapter(ch, text, ledger)
            return ch["story_key"], summary, text, info

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            futures = {pool.submit(_do_chapter, ch): ch for ch in pending}
            for future in as_completed(futures):
                ch = futures[future]
                try:
                    key, summary, text, info = future.result()
                    chapter_summaries[key] = summary
                    chapter_texts[key] = text
                    chapter_meta[key] = _meta_entry(info, summary)
                    stats.chapters_generated += 1
                    if info["path"] == "llm":
                        stats.api_calls += 1
                    print(f"  [{stats.chapters_generated}/{len(pending)}] {ch['code']} {ch['name']} ({len(summary)} chars)")
                except Exception as exc:  # noqa: BLE001
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
                        except Exception:  # noqa: BLE001
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

        def _do_event(ev_id: str, ev_data: dict) -> tuple[str, str, dict]:
            # Every event gets a real LLM summary, single-chapter included;
            # events with no readable text fall back to the sentinel.
            if not ev_data["parts"]:
                return ev_id, SENTINEL_NO_DIALOGUE, {"path": "sentinel"}
            full_text = "\n\n".join(ev_data["parts"])
            summary, info = _summarize_event(ev_data["event_name"], full_text, ev_id, ledger)
            return ev_id, summary, info

        sorted_events = sorted(events.items(), key=lambda x: len(x[1]["parts"]), reverse=True)
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            futures = {pool.submit(_do_event, eid, edata): eid for eid, edata in sorted_events}
            for future in as_completed(futures):
                ev_id = futures[future]
                ev_data = events[ev_id]
                try:
                    result_id, summary, info = future.result()
                    event_summaries[result_id] = summary
                    event_meta[result_id] = _meta_entry(info, summary)
                    stats.events_generated += 1
                    if info["path"] == "llm":
                        stats.api_calls += 1
                    print(f"  event {ev_data['event_name']}: {len(ev_data['parts'])} chapters → {len(summary)} chars")
                except Exception as exc:  # noqa: BLE001
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
    # Sidecars track the data files one-to-one: prune stale keys the same way
    chapter_meta = {k: v for k, v in chapter_meta.items() if k in chapter_summaries}
    event_meta = {k: v for k, v in event_meta.items() if k in event_summaries}
    (zh / "summaries.meta.json").write_text(
        json.dumps(chapter_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (zh / "event_summaries.meta.json").write_text(
        json.dumps(event_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"[summarize] done: chapters +{stats.chapters_generated}/{stats.chapters_failed}fail, "
          f"events +{stats.events_generated}/{stats.events_failed}fail, "
          f"api_calls={stats.api_calls}")
    return stats
