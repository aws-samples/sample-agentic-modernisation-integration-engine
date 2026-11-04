"""Reading answer text from Anthropic Messages responses that include thinking blocks."""

from __future__ import annotations

from utils.bedrock import response_text


def test_thinking_block_before_text_is_skipped():
    body = {
        "content": [
            {"type": "thinking", "thinking": "", "signature": "x"},
            {"type": "text", "text": "Architecture overview"},
        ]
    }
    assert response_text(body) == "Architecture overview"


def test_multiple_text_blocks_are_joined():
    body = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert response_text(body) == "ab"


def test_legacy_single_block_and_empty_bodies():
    assert response_text({"content": [{"text": "hi"}]}) == "hi"
    assert response_text({"content": []}) == ""
    assert response_text({}) == ""
