"""Model Context Protocol (MCP) client manager for external tools integration."""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from sudo.core.tools import ToolSpec, register_tool, TOOL_REGISTRY

# Global location of config
MCP_CONFIG_FILE = Path.home() / ".config" / "sudo" / "mcp.json"

ACTIVE_SERVERS: dict[str, subprocess.Popen] = {}
REGISTERED_MCP_TOOLS: list[str] = []


def load_mcp_config() -> dict:
    if not MCP_CONFIG_FILE.exists():
        # Create empty config
        MCP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        MCP_CONFIG_FILE.write_text(json.dumps({"mcpServers": {}}, indent=2), encoding="utf-8")
        return {"mcpServers": {}}
    try:
        return json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"mcpServers": {}}


def shutdown_mcp_servers() -> None:
    global ACTIVE_SERVERS, REGISTERED_MCP_TOOLS
    for name, proc in list(ACTIVE_SERVERS.items()):
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    ACTIVE_SERVERS.clear()
    
    for tool_name in REGISTERED_MCP_TOOLS:
        TOOL_REGISTRY.pop(tool_name, None)
    REGISTERED_MCP_TOOLS.clear()


def initialize_mcp_servers() -> None:
    shutdown_mcp_servers()
    config = load_mcp_config()
    servers = config.get("mcpServers", {})
    
    for name, s_cfg in servers.items():
        cmd = s_cfg.get("command")
        args = s_cfg.get("args", [])
        if not cmd:
            continue
            
        try:
            full_cmd = [cmd] + args
            shell = True if os.name == 'nt' else False
            proc = subprocess.Popen(
                full_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=shell
            )
            ACTIVE_SERVERS[name] = proc
            
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "sudo-cli", "version": "0.1.0"}
                }
            }
            proc.stdin.write(json.dumps(init_req) + "\n")
            proc.stdin.flush()
            
            resp_line = proc.stdout.readline()
            if not resp_line:
                continue
                
            init_notif = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            proc.stdin.write(json.dumps(init_notif) + "\n")
            proc.stdin.flush()
            
            list_req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            proc.stdin.write(json.dumps(list_req) + "\n")
            proc.stdin.flush()
            
            list_resp_line = proc.stdout.readline()
            if not list_resp_line:
                continue
            list_resp = json.loads(list_resp_line)
            tools = list_resp.get("result", {}).get("tools", [])
            
            for t in tools:
                t_name = t.get("name")
                t_desc = t.get("description", "")
                t_input_schema = t.get("inputSchema", {})
                t_properties = t_input_schema.get("properties", {})
                
                params = {}
                for prop_name, prop_val in t_properties.items():
                    params[prop_name] = {
                        "type": prop_val.get("type", "string"),
                        "description": prop_val.get("description", "")
                    }
                    
                def make_mcp_handler(srv_name=name, tl_name=t_name):
                    def handler(**kwargs):
                        proc_inst = ACTIVE_SERVERS.get(srv_name)
                        if not proc_inst:
                            return f"[MCP Error: Server '{srv_name}' not running]"
                        
                        call_req = {
                            "jsonrpc": "2.0",
                            "id": 100,
                            "method": "tools/call",
                            "params": {
                                "name": tl_name,
                                "arguments": kwargs
                            }
                        }
                        try:
                            proc_inst.stdin.write(json.dumps(call_req) + "\n")
                            proc_inst.stdin.flush()
                            resp_ln = proc_inst.stdout.readline()
                            if not resp_ln:
                                return "[MCP Error: Received empty response from server]"
                            call_resp = json.loads(resp_ln)
                            content = call_resp.get("result", {}).get("content", [])
                            res_text = ""
                            for c in content:
                                if c.get("type") == "text":
                                    res_text += c.get("text", "")
                            return res_text or f"[MCP Result: {call_resp}]"
                        except Exception as ex:
                            return f"[MCP Exception: {ex}]"
                    return handler
                
                spec = ToolSpec(
                    name=t_name,
                    description=t_desc,
                    parameters=params,
                    handler=make_mcp_handler()
                )
                register_tool(spec)
                REGISTERED_MCP_TOOLS.append(t_name)
                
        except Exception as e:
            pass
