"""Tests for output formatting utilities."""

from sudo.utils.output import (
    terminal_width,
    truncate,
    format_status,
    format_tree,
    format_check,
    format_cross,
)


def test_terminal_width_bounds():
    w = terminal_width()
    assert 20 <= w <= 70


def test_truncate_short_text():
    assert truncate("hello", max_lines=5) == "hello"


def test_truncate_long_text():
    text = "\n".join(f"line {i}" for i in range(10))
    result = truncate(text, max_lines=3)
    lines = result.splitlines()
    assert len(lines) == 4  # 3 lines + summary
    assert "+7 more" in result


def test_truncate_singular():
    text = "\n".join(f"line {i}" for i in range(5))
    result = truncate(text, max_lines=4)
    assert "+1 more line" in result


def test_truncate_plural():
    text = "\n".join(f"line {i}" for i in range(6))
    result = truncate(text, max_lines=4)
    assert "+2 more lines" in result


def test_format_status_empty():
    assert format_status({}) == ""


def test_format_status_basic():
    result = format_status({"provider": "groq", "model": "llama3"})
    assert "Provider" in result
    assert "groq" in result
    assert "Model" in result
    assert "llama3" in result


def test_format_status_none_value():
    result = format_status({"api_key": None})
    assert "(not set)" in result


def test_format_tree_empty():
    result = format_tree([], max_depth=2)
    assert "(no files)" in result


def test_format_tree_single_file():
    result = format_tree(["src/main.py"], max_depth=2)
    assert "src" in result
    assert "main.py" in result


def test_format_tree_multi_depth():
    paths = ["src/main.py", "src/utils/helpers.py", "README.md"]
    result = format_tree(paths, max_depth=2)
    assert "src" in result
    assert "main.py" in result
    assert "utils" in result
    assert "helpers.py" in result
    assert "README.md" in result


def test_format_check():
    result = format_check("all good")
    assert "[✓]" in result
    assert "all good" in result


def test_format_check_with_label():
    result = format_check("done", label="Test")
    assert "[✓]" in result
    assert "Test" in result
    assert "done" in result


def test_format_cross():
    result = format_cross("failed")
    assert "[✗]" in result
    assert "failed" in result


def test_format_cross_with_label():
    result = format_cross("error", label="Build")
    assert "[✗]" in result
    assert "Build" in result
