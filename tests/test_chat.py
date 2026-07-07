"""Tests for chat session utilities — the previously untested file."""

import os
import base64
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the functions we can test without needing a live LLM
from sudo.commands.chat import (
    get_context_limit,
    extract_content,
    load_multimodal_file,
    trim_context,
    parse_usage,
    stream_filter_think_tags,
)


class TestGetContextLimit:
    def test_gemini_gets_1m(self):
        assert get_context_limit("gemini-2.0-flash") == 1_000_000

    def test_claude_gets_200k(self):
        assert get_context_limit("claude-sonnet-4-20250514") == 200_000

    def test_gpt4_gets_128k(self):
        assert get_context_limit("gpt-4o") == 128_000
        assert get_context_limit("gpt-4-turbo") == 128_000

    def test_deepseek_gets_64k(self):
        assert get_context_limit("deepseek-chat") == 64_000

    def test_llama33_gets_128k(self):
        assert get_context_limit("llama-3.3-70b-versatile") == 128_000

    def test_llama31_gets_128k(self):
        assert get_context_limit("llama-3.1-8b-instant") == 128_000

    def test_old_llama_gets_8k(self):
        assert get_context_limit("llama-2-70b") == 8_000

    def test_unknown_model_gets_default(self):
        assert get_context_limit("unknown-model") == 32_000


class TestExtractContent:
    def test_openai_format(self):
        response = {"choices": [{"message": {"content": "Hello!"}}]}
        assert extract_content(response, "openai") == "Hello!"

    def test_anthropic_format(self):
        response = {"content": [{"text": "Hi there!"}]}
        assert extract_content(response, "anthropic") == "Hi there!"

    def test_google_format(self):
        response = {"candidates": [{"content": {"parts": [{"text": "Hey!"}]}}]}
        assert extract_content(response, "google") == "Hey!"

    def test_empty_response(self):
        assert extract_content({}, "openai") == ""
        assert extract_content({}, "anthropic") == ""
        assert extract_content({}, "google") == ""

    def test_malformed_response(self):
        assert extract_content({"choices": []}, "openai") == ""
        assert extract_content({"content": []}, "anthropic") == ""
        assert extract_content({"candidates": []}, "google") == ""


class TestLoadMultimodalFile:
    def test_load_image(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        result = load_multimodal_file(str(img))
        assert result is not None
        assert result["mime_type"] == "image/png"
        assert result["path"] == str(img)
        assert len(result["data"]) > 0

    def test_load_nonexistent(self):
        assert load_multimodal_file("/nonexistent/file.png") is None

    def test_rejects_large_files(self, tmp_path):
        big_file = tmp_path / "huge.png"
        # Write 21MB (over 20MB limit)
        big_file.write_bytes(b"\x00" * (21 * 1024 * 1024))
        result = load_multimodal_file(str(big_file))
        assert result is None

    def test_accepts_file_at_limit(self, tmp_path):
        ok_file = tmp_path / "ok.png"
        # Write exactly 20MB
        ok_file.write_bytes(b"\x00" * (20 * 1024 * 1024))
        result = load_multimodal_file(str(ok_file))
        assert result is not None

    def test_strips_quotes(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 10)
        result = load_multimodal_file(f'"{img}"')
        assert result is not None

    def test_fallback_mime_for_unknown(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_bytes(b"\x00" * 10)
        result = load_multimodal_file(str(f))
        assert result is not None
        assert result["mime_type"] == "image/jpeg"  # fallback


class TestTrimContext:
    def _make_messages(self, n):
        msgs = [{"role": "system", "content": "You are a bot."}]
        for i in range(n):
            msgs.append({"role": "user", "content": f"msg {i}"})
            msgs.append({"role": "assistant", "content": f"reply {i}"})
        return msgs

    def test_short_context_not_trimmed(self):
        msgs = self._make_messages(2)
        result = trim_context(msgs, "gpt-4o")
        assert len(result) == len(msgs)

    def test_long_context_is_trimmed(self):
        # Create enough messages to exceed context
        msgs = [{"role": "system", "content": "X" * 500}]
        for i in range(50):
            msgs.append({"role": "user", "content": "Y" * 500})
            msgs.append({"role": "assistant", "content": "Z" * 500})
        result = trim_context(msgs, "llama-2-70b")  # 8k context
        # Should be shorter than original
        assert len(result) < len(msgs)
        # System prompt always preserved
        assert result[0]["role"] == "system"

    def test_system_prompt_always_first(self):
        msgs = self._make_messages(20)
        result = trim_context(msgs, "gpt-4o")
        assert result[0]["role"] == "system"


class TestParseUsage:
    def test_openai_usage(self):
        response = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        assert parse_usage(response, "openai") == (100, 50)

    def test_anthropic_usage(self):
        response = {"usage": {"input_tokens": 200, "output_tokens": 75}}
        assert parse_usage(response, "anthropic") == (200, 75)

    def test_google_usage(self):
        response = {"usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 100}}
        assert parse_usage(response, "google") == (300, 100)

    def test_empty_response(self):
        assert parse_usage({}, "openai") == (0, 0)
        assert parse_usage({}, "anthropic") == (0, 0)
        assert parse_usage({}, "google") == (0, 0)


class TestStreamFilterThinkTags:
    def test_no_think_tags(self):
        stream = ["Hello, ", "world!", " How are ", "you?"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "Hello, world! How are you?"

    def test_simple_think_filtering(self):
        stream = ["Hello <think>secret thought</think> world!"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "Hello  world!"

    def test_split_think_tags(self):
        stream = ["Hello <thi", "nk>secret ", "thought</th", "ink> world!"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "Hello  world!"

    def test_multiple_think_tags(self):
        stream = ["A <think>1</think> B <think>2</think> C"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "A  B  C"

    def test_unclosed_think_tag(self):
        stream = ["A <think> unfinished"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "A "

    def test_regular_less_than_sign(self):
        stream = ["A < B and B < C"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "A < B and B < C"

    def test_prefix_lookalike(self):
        stream = ["Hello <thi", "s is not a tag"]
        result = list(stream_filter_think_tags(stream))
        assert "".join(result) == "Hello <this is not a tag"
