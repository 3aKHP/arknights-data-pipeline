"""Unit tests for Dialog/PopupDialog inline-text normalization."""

from akdp.story_normalize import ANONYMOUS, normalize_dialog_text, speaker_resolver

RESOLVER = speaker_resolver({
    "char_405_absin": {"name": "苦艾"},
    "char_188_helage": {"name": "赫拉格"},
})


def test_inline_dialog_becomes_named_line():
    raw = '[Dialog(head="char_405_absin", delay=1)]啊，赫拉格将军。\n'
    assert normalize_dialog_text(raw, RESOLVER) == '[name="苦艾"]啊，赫拉格将军。\n'


def test_inline_popup_dialog_becomes_named_line():
    raw = '[PopupDialog(head="char_188_helage")]盛大篇章已经落幕。\n'
    assert normalize_dialog_text(raw, RESOLVER) == '[name="赫拉格"]盛大篇章已经落幕。\n'


def test_trailing_text_form_joined():
    raw = '[Dialog(head="char_405_absin")]\n第一行。\n第二行。\n[Blocker(a=1)]\n'
    assert normalize_dialog_text(raw, RESOLVER) == '[name="苦艾"]第一行。\n第二行。\n[Blocker(a=1)]\n'


def test_unknown_head_degrades_to_anonymous():
    raw = '[Dialog(head="npc_unknown")]……\n'
    assert normalize_dialog_text(raw, RESOLVER) == f'[name="{ANONYMOUS}"]……\n'


def test_bare_dialog_staging_passes_through():
    raw = '[Dialog]\n[Blocker(a=1, fadetime=1, block=true)]\n'
    assert normalize_dialog_text(raw, RESOLVER) is raw  # unchanged, returned as-is


def test_no_dialog_returns_input_unchanged():
    raw = '[name="阿米娅"]你好，博士。\n纯散文行。\n'
    assert normalize_dialog_text(raw, RESOLVER) is raw
