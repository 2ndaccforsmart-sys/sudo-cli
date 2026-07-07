"""Tests for the Model Context Protocol (MCP) server integration."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from sudo.core.mcp import (
    load_mcp_config,
    initialize_mcp_servers,
    shutdown_mcp_servers,
    MCP_CONFIG_FILE,
    ACTIVE_SERVERS,
    REGISTERED_MCP_TOOLS,
)
from sudo.core.tools import TOOL_REGISTRY

def test_mcp_config_fallback(tmp_path):
    test_file = tmp_path / "sudo-config.json"
    with patch("sudo.core.config.CONFIG_FILE", test_file):
        config = load_mcp_config()
        assert config == {"mcpServers": {}}
        assert test_file.exists()


@patch("subprocess.Popen")
def test_initialize_and_shutdown_mcp_servers(mock_popen, tmp_path):
    test_file = tmp_path / "sudo-config.json"
    config_data = {
        "mcp_servers": {
            "test-server": {
                "command": "python",
                "args": ["-m", "http.server"]
            }
        }
    }
    test_file.write_text(json.dumps(config_data))
    
    mock_proc = MagicMock()
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {
                "name": "mcp_tool",
                "description": "an mcp test tool",
                "inputSchema": {"properties": {"arg": {"type": "string"}}}
            }
        ]}})
    ]
    mock_popen.return_value = mock_proc
    
    with patch("sudo.core.config.CONFIG_FILE", test_file):
        initialize_mcp_servers()
        assert "test-server" in ACTIVE_SERVERS
        assert "mcp_tool" in TOOL_REGISTRY
        assert "mcp_tool" in REGISTERED_MCP_TOOLS
        
        mock_proc.stdout.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 100, "result": {"content": [{"type": "text", "text": "hello"}]}})
        ]
        res = TOOL_REGISTRY["mcp_tool"].handler(arg="test")
        assert res == "hello"
        
        shutdown_mcp_servers()
        assert "test-server" not in ACTIVE_SERVERS
        assert "mcp_tool" not in TOOL_REGISTRY
