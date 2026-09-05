"""Behavioral tests for the gated, ledgered summarize step.

All LLM traffic is mocked at the single-HTTP-call boundary (_chat_once);
backoff sleeps are disabled. These tests cover: acceptance-gate retries,
fail-closed budgets, transport error classification, the single-chapter
event LLM path, sentinel handling, sidecar writing/pruning, and ledger
content (including the no-raw-text privacy boundary).
"""

import hashlib
import itertools
import io
import json
from pathlib import Path

import pytest

from akdp import summarize as S

GOOD_CHAPTER = "话" * 60 + "。"
GOOD_EVENT = "话" * 130 + "。"
SENTINEL = "（无对话内容）"


def _patch_env(monkeypatch):
    monkeypatch.setattr(S, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(S, "LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setattr(S, "LLM_MODEL", "test-model")
    monkeypatch.setattr(S, "_sleep_jitter", lambda step: None)


def _resp(content, finish="stop"):
    return S._CallResult(
        content=content, finish_reason=finish, model="test-model",
        response_id="resp-1", prompt_tokens=100, completion_tokens=50,
        latency_ms=5,
    )


def _script(items):
    """Queue of responses/exceptions consumed by each _chat_once attempt."""
    it = iter(items)
    calls = []

    def fake(messages, max_tokens):
        calls.append(max_tokens)
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    return fake, calls


def _make_candidate(tmp_path, *, chapter_count=1, empty_chapter=False):
    cand = tmp_path / "candidate"
    zh = cand / "zh_CN"
    (zh / "gamedata/excel").mkdir(parents=True)
    review = {"act1": {"name": "测试活动", "infoUnlockDatas": []}}
    for i in range(chapter_count):
        key = f"activities/act1/ch{i}"
        review["act1"]["infoUnlockDatas"].append({
            "storyTxt": key, "storyCode": f"EP{i}", "storyName": f"章节{i}",
            "storySort": i, "avgTag": "测试",
        })
        story_dir = zh / "gamedata/story/activities/act1"
        story_dir.mkdir(parents=True, exist_ok=True)
        story = {"storyList": [] if empty_chapter else [
            {"prop": "name", "attributes": {"name": "阿米娅", "content": "剧情正文。" * 80}},
        ]}
        (story_dir / f"ch{i}.json").write_text(
            json.dumps(story, ensure_ascii=False), encoding="utf-8")
    (zh / "gamedata/excel/story_review_table.json").write_text(
        json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return cand


def _ledger_records(tmp_path):
    return [json.loads(line) for line in
            (tmp_path / "llm-ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_gate_reject_then_regenerate_passes(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, calls = _script([
        _resp("太短。"),        # chapter attempt 1: gate rejects
        _resp(GOOD_CHAPTER),   # chapter attempt 2: accepted
        _resp(GOOD_EVENT),     # event: accepted first try
    ])
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand, run_meta={"version_id": "v-test"})

    assert stats.failed_details == []
    assert stats.chapters_generated == 1 and stats.events_generated == 1
    assert stats.api_calls == 2
    zh = cand / "zh_CN"
    assert json.loads((zh / "summaries.json").read_text(encoding="utf-8"))[
        "activities/act1/ch0"] == GOOD_CHAPTER
    assert json.loads((zh / "event_summaries.json").read_text(encoding="utf-8"))["act1"] == GOOD_EVENT

    records = _ledger_records(tmp_path)
    assert len(records) == 3
    assert records[0]["verdict"] == "reject" and records[0]["reject_reason"] == "too_short"
    assert records[0]["retry_reason"] is None
    assert records[1]["verdict"] == "pass" and records[1]["retry_reason"] == "too_short"
    assert records[2]["verdict"] == "pass" and records[2]["level"] == "event"
    assert records[1]["output_sha256"] == hashlib.sha256(GOOD_CHAPTER.encode()).hexdigest()

    meta = json.loads((zh / "summaries.meta.json").read_text(encoding="utf-8"))
    entry = meta["activities/act1/ch0"]
    assert entry["path"] == "llm" and entry["finish_reason"] == "stop"


def test_ledger_carries_full_detail_but_no_story_text(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, _ = _script([_resp(GOOD_CHAPTER), _resp(GOOD_EVENT)])
    monkeypatch.setattr(S, "_chat_once", fake)
    S.run_summarize(cand, run_meta={"version_id": "v-test"})

    raw = (tmp_path / "llm-ledger.jsonl").read_text(encoding="utf-8")
    # full provider detail belongs in the (privately shipped) ledger...
    assert '"endpoint": "https://llm.test/v1"' in raw
    assert '"requested_model": "test-model"' in raw
    # ...but never the prompt or story text
    assert "剧情正文" not in raw
    assert "阿米娅" not in raw
    rec = _ledger_records(tmp_path)[0]
    assert rec["input_chars"] > 0 and len(rec["input_sha256"]) == 64
    assert rec["version_id"] == "v-test"


def test_gate_budget_exhaustion_fails_closed(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, calls = _script(itertools.repeat(_resp("太短。")))
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    assert stats.chapters_failed == 1 and stats.events_failed == 1
    errors = " ".join(d["error"] for d in stats.failed_details)
    assert "too_short" in errors
    # 1 initial + 2 regen attempts per logical call, for both levels
    assert len(calls) == 6
    assert all(rec["verdict"] == "reject" for rec in _ledger_records(tmp_path))
    zh = cand / "zh_CN"
    summaries = json.loads((zh / "summaries.json").read_text(encoding="utf-8"))
    assert "activities/act1/ch0" not in summaries


def test_transport_401_is_deterministic_failure(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, calls = _script(itertools.repeat(
        S._TransportError("HTTP 401: bad key", status=401)))
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    assert stats.chapters_failed == 1 and stats.events_failed == 1
    assert "failed after 1 attempts" in stats.failed_details[0]["error"]
    assert len(calls) == 2  # exactly one attempt per logical call
    records = _ledger_records(tmp_path)
    assert all(r["verdict"] == "error" and r["reject_reason"] == "transport:401"
               for r in records)


def test_transport_429_retries_with_backoff_budget(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, calls = _script(itertools.repeat(
        S._TransportError("HTTP 429: rate limited", status=429)))
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    assert stats.chapters_failed == 1 and stats.events_failed == 1
    # 1 initial + 3 transport retries per logical call, two logical calls
    assert len(calls) == 8
    assert "failed after 4 attempts" in stats.failed_details[0]["error"]
    records = _ledger_records(tmp_path)
    assert all(r["verdict"] == "error" and r["reject_reason"] == "transport:429"
               for r in records)


def test_terminal_finish_reason_never_regenerates(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, calls = _script(itertools.repeat(_resp(GOOD_CHAPTER, finish="content_filter")))
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    assert stats.chapters_failed == 1
    assert "finish_reason:content_filter" in stats.failed_details[0]["error"]
    # one attempt for the chapter, one for the event — no re-rolls
    assert len(calls) == 2


def test_single_chapter_event_gets_real_llm_summary(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path, chapter_count=1)
    fake, _ = _script([_resp(GOOD_CHAPTER), _resp(GOOD_EVENT)])
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    # the pre-change code truncated raw chapter text instead of calling
    assert stats.api_calls == 2
    event = json.loads(
        (cand / "zh_CN/event_summaries.json").read_text(encoding="utf-8"))["act1"]
    assert event == GOOD_EVENT
    assert not event.startswith("--- EP0")


def test_empty_chapter_and_event_use_sentinel_without_api(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path, empty_chapter=True)
    fake, calls = _script([])
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    assert calls == []
    assert stats.api_calls == 0
    zh = cand / "zh_CN"
    summaries = json.loads((zh / "summaries.json").read_text(encoding="utf-8"))
    events = json.loads((zh / "event_summaries.json").read_text(encoding="utf-8"))
    assert summaries["activities/act1/ch0"] == SENTINEL
    assert events["act1"] == SENTINEL
    chapter_meta = json.loads((zh / "summaries.meta.json").read_text(encoding="utf-8"))
    event_meta = json.loads((zh / "event_summaries.meta.json").read_text(encoding="utf-8"))
    assert chapter_meta["activities/act1/ch0"]["path"] == "sentinel"
    assert event_meta["act1"]["path"] == "sentinel"


def test_reused_entries_prune_stale_keys_in_data_and_sidecar(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    zh = cand / "zh_CN"
    (zh / "summaries.json").write_text(json.dumps({
        "activities/act1/ch0": GOOD_CHAPTER,
        "stale/key": "旧条目" * 20,
    }, ensure_ascii=False), encoding="utf-8")
    (zh / "event_summaries.json").write_text(json.dumps({
        "act1": GOOD_EVENT,
    }, ensure_ascii=False), encoding="utf-8")
    (zh / "summaries.meta.json").write_text(json.dumps({
        "activities/act1/ch0": {"path": "llm"},
        "stale/key": {"path": "llm"},
    }, ensure_ascii=False), encoding="utf-8")
    (zh / "event_summaries.meta.json").write_text(json.dumps({
        "act1": {"path": "llm"},
    }, ensure_ascii=False), encoding="utf-8")
    fake, calls = _script([])
    monkeypatch.setattr(S, "_chat_once", fake)

    stats = S.run_summarize(cand)

    assert calls == []  # fully cached, no LLM traffic
    assert stats.chapters_reused == 1 and stats.events_reused == 1
    assert stats.api_calls == 0
    summaries = json.loads((zh / "summaries.json").read_text(encoding="utf-8"))
    chapter_meta = json.loads((zh / "summaries.meta.json").read_text(encoding="utf-8"))
    assert "stale/key" not in summaries and "stale/key" not in chapter_meta
    assert "activities/act1/ch0" in chapter_meta
    assert not (tmp_path / "llm-ledger.jsonl").exists()


def test_no_api_key_ships_partial_and_reports_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "LLM_API_KEY", "")
    monkeypatch.setattr(S, "_sleep_jitter", lambda step: None)
    cand = _make_candidate(tmp_path)

    stats = S.run_summarize(cand)

    assert stats.chapters_failed == 1 and stats.events_failed == 1
    assert stats.api_calls == 0
    assert not (tmp_path / "llm-ledger.jsonl").exists()


def test_extra_body_merges_provider_extensions(monkeypatch):
    monkeypatch.setattr(S, "LLM_MODEL", "test-model")
    monkeypatch.setattr(S, "LLM_EXTRA_BODY", {"thinking": {"type": "none"}})
    body = S._request_body([{"role": "user", "content": "hi"}], 600)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 600
    assert body["thinking"] == {"type": "none"}


def test_extra_body_invalid_json_degrades_to_empty(monkeypatch, capsys):
    monkeypatch.setenv("LLM_EXTRA_BODY", "{not json")
    assert S._load_extra_body() == {}
    assert "LLM_EXTRA_BODY" in capsys.readouterr().out


def test_extra_body_non_object_degrades_to_empty(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", "[1, 2]")
    assert S._load_extra_body() == {}


def test_extra_body_reserved_keys_are_dropped(monkeypatch, capsys):
    monkeypatch.setenv(
        "LLM_EXTRA_BODY",
        '{"model": "evil", "thinking": {"type": "disabled"}, "max_tokens": 1}',
    )
    assert S._load_extra_body() == {"thinking": {"type": "disabled"}}
    out = capsys.readouterr().out
    assert "max_tokens" in out and "model" in out


def test_provider_error_body_never_reaches_public_failures(tmp_path, monkeypatch, capsys):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    secret = "private-provider-error-and-prompt"

    def fail(*args, **kwargs):
        raise S.urllib.error.HTTPError(
            "https://llm.test/v1", 400, "bad request", {}, io.BytesIO(secret.encode()))

    monkeypatch.setattr(S.urllib.request, "urlopen", fail)
    stats = S.run_summarize(cand)
    assert stats.failed_details
    assert secret not in json.dumps(stats.to_dict()) + capsys.readouterr().out


def test_invalid_extra_body_warning_does_not_echo_secret(monkeypatch, capsys):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"private":"do-not-log"')
    S._load_extra_body()
    assert "do-not-log" not in capsys.readouterr().out


def test_non_string_content_is_rejected_and_ledgered(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    payload = {"choices": [{"message": {"content": [GOOD_EVENT]}, "finish_reason": "stop"}]}
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    stats = S.run_summarize(cand)
    assert stats.chapters_failed == 1 and stats.events_failed == 1
    assert len(_ledger_records(tmp_path)) == 2


def test_ledger_records_response_and_run_identity(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, _ = _script([_resp(GOOD_CHAPTER), _resp(GOOD_EVENT)])
    monkeypatch.setattr(S, "_chat_once", fake)
    S.run_summarize(cand, run_meta={"publication_revision": 2, "pipeline_run_id": "repair-1"})
    rec = _ledger_records(tmp_path)[0]
    assert rec["response_id"] == "resp-1"
    assert rec["publication_revision"] == 2
    assert rec["pipeline_run_id"] == "repair-1"


def test_model_cannot_claim_no_dialogue_sentinel(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    cand = _make_candidate(tmp_path)
    fake, _ = _script(itertools.repeat(_resp(SENTINEL)))
    monkeypatch.setattr(S, "_chat_once", fake)
    stats = S.run_summarize(cand)
    assert stats.chapters_failed == 1 and stats.events_failed == 1


def test_endpoint_credentials_are_removed_from_ledger(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(S, "LLM_BASE_URL", "https://user:private-password@llm.test/v1?key=private-query#private-fragment")
    fake, _ = _script([_resp(GOOD_CHAPTER), _resp(GOOD_EVENT)])
    monkeypatch.setattr(S, "_chat_once", fake)
    S.run_summarize(_make_candidate(tmp_path))
    ledger = (tmp_path / "llm-ledger.jsonl").read_text()
    assert "private-" not in ledger
    assert _ledger_records(tmp_path)[0]["endpoint"] == "https://llm.test/v1"


@pytest.mark.parametrize("field", ["model", "id"])
def test_invalid_response_metadata_is_ledgered(tmp_path, monkeypatch, field):
    _patch_env(monkeypatch)
    payload = {"choices": [{"message": {"content": GOOD_EVENT}, "finish_reason": "stop"}], field: ["invalid"]}
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    stats = S.run_summarize(_make_candidate(tmp_path))
    assert stats.chapters_failed == 1 and stats.events_failed == 1
    assert len(_ledger_records(tmp_path)) == 2
