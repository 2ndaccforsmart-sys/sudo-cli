"""Tests for the structured tool definitions and execution engine."""

import os
import tempfile
from pathlib import Path

from sudo.core.tools import (
    ToolSpec,
    register_tool,
    get_tool_schemas,
    parse_tool_calls,
    execute_tool,
    parse_and_execute_tools,
    TOOL_REGISTRY,
)


def test_tool_registry_has_core_tools():
    assert "read_file" in TOOL_REGISTRY
    assert "write_file" in TOOL_REGISTRY
    assert "delete_file" in TOOL_REGISTRY
    assert "run_command" in TOOL_REGISTRY
    assert "list_dir" in TOOL_REGISTRY


def test_tool_schemas_are_valid():
    schemas = get_tool_schemas()
    assert len(schemas) >= 4  # at least 4 active tools
    for s in schemas:
        assert s["type"] == "function"
        func = s["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        assert "properties" in func["parameters"]


def test_list_dir_is_disabled():
    spec = TOOL_REGISTRY["list_dir"]
    assert spec.disabled is True


def test_execute_unknown_tool():
    result = execute_tool("nonexistent_tool", {})
    assert "Unknown tool" in result


def test_execute_read_file_not_found():
    result = execute_tool("read_file", {"path": "/nonexistent/path/file.txt"})
    assert "does not exist" in result


def test_execute_read_file_success():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello world")
        tmp = f.name
    try:
        result = execute_tool("read_file", {"path": tmp})
        assert "hello world" in result
        assert "[Tool Output" in result
    finally:
        os.unlink(tmp)


def test_execute_write_file_success():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "test.txt")
        result = execute_tool("write_file", {"path": p, "content": "test content"})
        assert "File written" in result
        assert Path(p).read_text() == "test content"


def test_execute_delete_file_success():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "delete_me.txt")
        Path(p).write_text("delete me")
        result = execute_tool("delete_file", {"path": p})
        assert "File" in result and "deleted" in result
        assert not os.path.exists(p)


def test_execute_run_command_success():
    result = execute_tool("run_command", {"cmd": "echo hello", "timeout": 10})
    assert "hello" in result
    assert "exit code: 0" in result


def test_execute_run_command_failure():
    result = execute_tool("run_command", {"cmd": "false"})
    assert "exit code: 1" in result


def test_parse_legacy_read_xml():
    text = 'Some text <tool:read_file path="foo.py"/> more text'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "read_file"
    assert calls[0]["arguments"]["path"] == "foo.py"


def test_parse_legacy_write_xml():
    text = '<tool:write_file path="out.py">print("hi")</tool:write_file>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert calls[0]["arguments"]["path"] == "out.py"
    assert "print" in calls[0]["arguments"]["content"]


def test_parse_legacy_run_command_xml():
    text = '<tool:run_command cmd="ls -la"/>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "run_command"
    assert calls[0]["arguments"]["cmd"] == "ls -la"
    assert calls[0]["arguments"]["timeout"] == 60


def test_parse_and_execute_round_trip():
    text = '<tool:run_command cmd="echo hello"/>'
    had, output = parse_and_execute_tools(text)
    assert had is True
    assert "hello" in output


def test_no_tool_call_returns_false():
    had, output = parse_and_execute_tools("Just some regular text.")
    assert had is False
    assert output == ""


def test_custom_tool_registration():
    was_called = []

    def my_handler(msg: str) -> str:
        was_called.append(msg)
        return f"Echo: {msg}"

    spec = ToolSpec(
        name="test_custom",
        description="A test tool",
        parameters={"msg": {"type": "string", "description": "A message"}},
        handler=my_handler,
    )
    register_tool(spec)
    assert "test_custom" in TOOL_REGISTRY

    result = execute_tool("test_custom", {"msg": "hello"})
    assert result == "Echo: hello"
    assert was_called == ["hello"]

    # Cleanup
    del TOOL_REGISTRY["test_custom"]
