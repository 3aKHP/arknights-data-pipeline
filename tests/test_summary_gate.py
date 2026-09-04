"""Verdict matrix for the summary acceptance gate."""

import json

from akdp import summary_gate as gate


def _body(n: int) -> str:
    """A summary body of n filler chars plus a sentence-final period."""
    return "话" * n + "。"


class TestAcceptSummary:
    def test_empty_variants_rejected(self):
        for text in (None, "", "   \n\t "):
            ok, reason = gate.accept_summary(gate.CHAPTER, text)
            assert not ok
            assert reason == "empty"

    def test_sentinel_accepted_at_any_level(self):
        assert gate.accept_summary(gate.CHAPTER, gate.SENTINEL_NO_DIALOGUE)[0]
        assert gate.accept_summary(gate.EVENT, gate.SENTINEL_NO_DIALOGUE)[0]

    def test_length_floor_chapter(self):
        assert gate.accept_summary(gate.CHAPTER, _body(49)) == (True, None)
        ok, reason = gate.accept_summary(gate.CHAPTER, _body(48))
        assert (ok, reason) == (False, "too_short")

    def test_length_floor_event(self):
        assert gate.accept_summary(gate.EVENT, _body(119)) == (True, None)
        ok, reason = gate.accept_summary(gate.EVENT, _body(118))
        assert (ok, reason) == (False, "too_short")

    def test_truncated_terminal_blacklist(self):
        for tail in ("话", "A", "7", "，", ",", ";", "；", "、", "：", ":", "-", "—", "~", "（"):
            ok, reason = gate.accept_summary(gate.CHAPTER, _body(60)[:-1] + tail)
            assert (ok, reason) == (False, "truncated_terminal"), tail

    def test_legal_endings_accepted(self):
        for tail in ("。", "！", "？", "…", "”", "』", "」", "）", ")", "》"):
            assert gate.accept_summary(gate.CHAPTER, _body(60)[:-1] + tail)[0], tail

    def test_format_pollution_prompt_echo(self):
        for prefix in ("摘要：", "梗概：", "  摘要:", "梗概:  "):
            ok, reason = gate.accept_summary(gate.CHAPTER, prefix + _body(60))
            assert (ok, reason) == (False, "format_pollution"), prefix

    def test_format_pollution_code_fence(self):
        text = _body(60) + "\n```json\n{}\n```"
        assert gate.accept_summary(gate.CHAPTER, text) == (False, "format_pollution")

    def test_format_pollution_json_payload(self):
        text = json.dumps(
            {"choices": [{"message": {"content": "话" * 60}}]},
            ensure_ascii=False,
        )
        assert len(text) >= gate.MIN_LENGTH[gate.CHAPTER]
        assert text.rstrip()[-1] == "}"  # would pass terminal check
        assert gate.accept_summary(gate.CHAPTER, text) == (False, "format_pollution")

    def test_plain_good_summary_passes(self):
        text = (
            "博士在罗德岛的作战会议上确认了下一步的行动方针，众人各自领命分散准备，"
            "整座舰船在夜色中恢复了往日的秩序与忙碌，而远方的危机仍在悄然逼近。"
        )
        assert gate.accept_summary(gate.CHAPTER, text) == (True, None)


class TestClassifyFinishReason:
    def test_accept(self):
        assert gate.classify_finish_reason("stop") == "accept"

    def test_retry(self):
        for reason in ("insufficient_system_resource", "network_error", "error"):
            assert gate.classify_finish_reason(reason) == "retry", reason

    def test_fail_known_bad(self):
        for reason in ("length", "content_filter", "sensitive", "tool_calls"):
            assert gate.classify_finish_reason(reason) == "fail", reason

    def test_fail_missing_and_unknown(self):
        assert gate.classify_finish_reason(None) == "fail"
        assert gate.classify_finish_reason("brand_new_value") == "fail"
