"""Tests for the structured tool definitions and execution engine."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def test_gcs_tools_registered():
    assert "gcs_list_files" in TOOL_REGISTRY
    assert "gcs_read_file" in TOOL_REGISTRY
    assert "gcs_write_file" in TOOL_REGISTRY
    assert "gcs_delete_file" in TOOL_REGISTRY
    assert "gcs_make_directory" in TOOL_REGISTRY


@patch("sudo.core.tools._get_gcs_client")
def test_execute_gcs_list_files(mock_get_client):
    mock_client = MagicMock()
    mock_client.list_files.return_value = [
        {"name": "file1.txt", "size": 1024, "updated": "2026-07-07T12:00:00"}
    ]
    mock_get_client.return_value = mock_client
    
    result = execute_tool("gcs_list_files", {"prefix": "test"})
    assert "file1.txt" in result
    assert "1.0 KB" in result


@patch("sudo.core.tools._get_gcs_client")
def test_execute_gcs_read_file(mock_get_client):
    mock_client = MagicMock()
    mock_client.read_file_text.return_value = "gcs file content"
    mock_get_client.return_value = mock_client
    
    result = execute_tool("gcs_read_file", {"path": "test.txt"})
    assert "gcs file content" in result


@patch("sudo.core.tools._get_gcs_client")
def test_execute_gcs_write_file(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    result = execute_tool("gcs_write_file", {"path": "test.txt", "content": "hello gcs"})
    assert "successfully written to GCS" in result
    mock_client._bucket.blob.assert_called_with("test.txt")


@patch("sudo.core.tools._get_gcs_client")
def test_execute_gcs_delete_file(mock_get_client):
    mock_client = MagicMock()
    mock_client.delete_file.return_value = True
    mock_get_client.return_value = mock_client
    
    result = execute_tool("gcs_delete_file", {"path": "test.txt"})
    assert "successfully deleted from GCS" in result


@patch("sudo.core.tools._get_gcs_client")
def test_execute_gcs_make_directory(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    result = execute_tool("gcs_make_directory", {"path": "folder"})
    assert "successfully created in GCS" in result
    mock_client._bucket.blob.assert_called_with("folder/")


def test_save_skill_tool_registered():
    assert "save_skill" in TOOL_REGISTRY


def test_parse_legacy_save_skill_xml():
    text = '<tool:save_skill name="test_skill" description="my test skill">do some magic</tool:save_skill>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "save_skill"
    assert calls[0]["arguments"]["name"] == "test_skill"
    assert calls[0]["arguments"]["description"] == "my test skill"
    assert calls[0]["arguments"]["system_prompt"] == "do some magic"


def test_execute_save_skill(tmp_path):
    test_file = tmp_path / "skills.json"
    with patch("sudo.core.skills.SKILLS_FILE", test_file):
        result = execute_tool("save_skill", {
            "name": "refactor",
            "description": "Refactor code",
            "system_prompt": "Refactor python code format"
        })
        assert "successfully saved" in result
        
        # Verify it got saved
        from sudo.core.skills import load_skills
        skills = load_skills()
        assert "refactor" in skills
        assert skills["refactor"]["description"] == "Refactor code"


def test_browse_tool_registered():
    assert "browse" in TOOL_REGISTRY


def test_parse_legacy_browse_xml():
    text = '<tool:browse url="https://example.com"/>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "browse"
    assert calls[0]["arguments"]["url"] == "https://example.com"


@patch("httpx.get")
def test_execute_browse_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body><h1>Hello World</h1><script>alert(1)</script></body></html>"
    mock_get.return_value = mock_resp
    
    result = execute_tool("browse", {"url": "https://example.com"})
    assert "Hello World" in result
    assert "alert" not in result  # script tags cleaned


def test_github_push_tool_registered():
    assert "github_push" in TOOL_REGISTRY


def test_parse_legacy_github_push_xml():
    text = '<tool:github_push commit_message="feat: cool feature"/>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "github_push"
    assert calls[0]["arguments"]["commit_message"] == "feat: cool feature"


@patch("subprocess.run")
def test_execute_github_push(mock_run):
    mock_res = MagicMock()
    mock_res.stdout = "done"
    mock_res.stderr = ""
    mock_run.return_value = mock_res
    
    result = execute_tool("github_push", {"commit_message": "feat: test", "branch": "main"})
    assert "git add output" in result
    assert "git commit output" in result
    assert "git push output" in result


def test_gcs_upload_tool_registered():
    assert "gcs_upload" in TOOL_REGISTRY


def test_parse_legacy_gcs_upload_xml():
    text = '<tool:gcs_upload local_path="local.txt" gcs_dest_path="remote.txt"/>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "gcs_upload"
    assert calls[0]["arguments"]["local_path"] == "local.txt"
    assert calls[0]["arguments"]["gcs_dest_path"] == "remote.txt"


@patch("sudo.core.tools._get_gcs_client")
def test_execute_gcs_upload_file(mock_get_client, tmp_path):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    local_file = tmp_path / "test.txt"
    local_file.write_text("file content")
    
    result = execute_tool("gcs_upload", {
        "local_path": str(local_file),
        "gcs_dest_path": "cloud.txt"
    })
    assert "successfully uploaded" in result
    mock_client._bucket.blob.assert_called_with("cloud.txt")



