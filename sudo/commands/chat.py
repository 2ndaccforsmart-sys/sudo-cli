"""'sudo chat' command — interactive AI chat session."""

from __future__ import annotations

import os
import sys
import time
import re
import json
from typing import Generator, Any

from sudo.core.config import load, save
from sudo.core.provider import PROVIDER_REGISTRY, ProviderFactory, BaseProvider
from sudo.core.session import SessionManager
from sudo.utils.output import terminal_width
from sudo.utils.banner import print_banner
from sudo import __version__


SYSTEM_PROMPT = (
    "You are SUDO, an autonomous AI coding assistant running in Android Termux. "
    "You have full access to the user's workspace, files, and shell. "
    "To interact with the environment, you must use the following XML tags in your response. "
    "Do NOT combine multiple tool calls in a single turn. Only call one tool at a time, wait for the tool output, then decide the next action.\n\n"
    "Available tools:\n"
    "1. Read a file:\n"
    "<tool:read_file path=\"relative/path/to/file\"/>\n\n"
    "2. Write/overwrite a file:\n"
    "<tool:write_file path=\"relative/path/to/file\">\n[file contents]\n</tool:write_file>\n\n"
    "3. List directory contents:\n"
    "<tool:list_dir path=\"relative/path/to/dir\"/>\n\n"
    "4. Delete a file or directory:\n"
    "<tool:delete_file path=\"relative/path/to/file_or_dir\"/>\n\n"
    "5. Run a shell command:\n"
    "<tool:run_command cmd=\"command to execute\"/>\n\n"
    "When you run a tool, the output of the tool will be provided to you in the next turn. "
    "Always state what you are doing before calling a tool, and keep your explanations brief. "
    "Do not talk too much; focus on executing tasks."
)


def register(subparsers) -> None:
    p = subparsers.add_parser("chat", help="Start an interactive chat session with the AI agent")
    p.set_defaults(func=lambda args: run_chat(args))


def get_context_limit(model_name: str) -> int:
    model_name = model_name.lower()
    if "gemini" in model_name:
        return 1000000
    if "claude" in model_name:
        return 200000
    if "gpt-4" in model_name or "gpt-4o" in model_name:
        return 128000
    if "deepseek" in model_name:
        return 64000
    if "llama" in model_name:
        if "3.3" in model_name or "3.1" in model_name:
            return 128000
        return 8000
    return 32000  # default fallback


def extract_content(response: dict, api_type: str) -> str:
    try:
        if api_type == "google":
            candidates = response.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
        elif api_type == "anthropic":
            content = response.get("content", [])
            if content:
                return content[0].get("text", "")
        else:
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
    except Exception:
        pass
    return ""


def chat_stream(provider: BaseProvider, messages: list[dict], **kwargs) -> Generator[str, None, None]:
    """Stream chat responses from the provider, fallback to non-stream if needed."""
    api_type = provider.defn.api_type
    
    try:
        import httpx
        
        if api_type == "openai":
            body = {"model": provider.model, "messages": messages, "stream": True, **kwargs}
            base = provider.base_url.rstrip('/')
            url = f"{base}/chat/completions"
            if base.endswith('/v1'):
                pass
            else:
                url = f"{base}/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
            }
            with httpx.stream("POST", url, headers=headers, json=body, timeout=60) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                        except Exception:
                            pass
                            
        elif api_type == "anthropic":
            system_msg = None
            anthropic_messages = []
            for m in messages:
                if m["role"] == "system":
                    system_msg = m["content"]
                else:
                    anthropic_messages.append({"role": m["role"], "content": m["content"]})
                    
            body = {"model": provider.model, "messages": anthropic_messages, "max_tokens": 4096, "stream": True, **kwargs}
            if system_msg:
                body["system"] = system_msg
            body.pop("max_completion_tokens", None)
            
            url = f"{provider.base_url.rstrip('/')}/messages"
            headers = {
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            with httpx.stream("POST", url, headers=headers, json=body, timeout=120) as resp:
                resp.raise_for_status()
                event_name = None
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event_name = line[7:].strip()
                    elif line.startswith("data: "):
                        data_str = line[6:].strip()
                        if event_name == "content_block_delta":
                            try:
                                data = json.loads(data_str)
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                            except Exception:
                                pass
                                
        elif api_type == "google":
            gemini_contents = []
            system_text = None
            for m in messages:
                if m["role"] == "system":
                    system_text = m["content"]
                    continue
                role = "user" if m["role"] == "user" else "model"
                gemini_contents.append({"role": role, "parts": [{"text": m["content"]}]})
                
            body = {"contents": gemini_contents, **kwargs}
            if system_text:
                body["system_instruction"] = {"parts": [{"text": system_text}]}
            model_name = provider.model
            if not model_name.startswith("models/"):
                model_name = f"models/{model_name}"
                
            url = f"{provider.base_url}/{model_name}:streamGenerateContent?key={provider.api_key}"
            
            with httpx.stream("POST", url, json=body, timeout=120) as resp:
                resp.raise_for_status()
                buffer = ""
                for chunk in resp.iter_text():
                    buffer += chunk
                    matches = list(re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', buffer))
                    if matches:
                        for match in matches:
                            text_val = match.group(1)
                            try:
                                text_val = json.loads(f'"{text_val}"')
                            except Exception:
                                pass
                            yield text_val
                        buffer = buffer[matches[-1].end():]
        else:
            res = provider.chat(messages, **kwargs)
            yield extract_content(res, api_type)
            
    except Exception as e:
        try:
            res = provider.chat(messages, **kwargs)
            yield extract_content(res, api_type)
        except Exception as inner_e:
            yield f"\n[Streaming error: {e}. Fallback error: {inner_e}]"


def print_status_bar(model: str, messages: list[dict], last_response_time: float, start_time: float) -> None:
    tw = terminal_width()
    
    total_chars = sum(len(m["content"]) for m in messages)
    tokens = total_chars // 4
    
    ctx_limit = get_context_limit(model)
    
    if tokens == 0:
        ctx_text = "--"
        bar_pct = 0
        pct_text = "--"
    else:
        if tokens >= 1000:
            ctx_text = f"{tokens/1000:.1f}k"
        else:
            ctx_text = str(tokens)
            
        bar_pct = min(100, int((tokens / ctx_limit) * 100))
        pct_text = f"{bar_pct}%"
        
    if last_response_time < 0:
        time_text = "--s"
    else:
        time_text = f"{int(round(last_response_time))}s"
        
    elapsed = int(time.time() - start_time)
    if elapsed >= 60:
        elapsed_text = f"{elapsed // 60}m"
    else:
        elapsed_text = f"{elapsed}s"
        
    fixed_len = len(f"⚡ {model} | ctx {ctx_text} | [] {pct_text} | {time_text} | ⏰{elapsed_text}")
    available_bar_space = tw - fixed_len - 2
    bar_width = max(3, min(15, available_bar_space))
    
    num_filled = min(bar_width, int((bar_pct / 100) * bar_width))
    num_empty = bar_width - num_filled
    bar = "█" * num_filled + "░" * num_empty
    
    raw_status = f"⚡ {model} | ctx {ctx_text} | [{bar}] {pct_text} | {time_text} | ⏰{elapsed_text}"
    
    if len(raw_status) > tw:
        raw_status = raw_status[:tw]
        padding = ""
    else:
        padding = " " * (tw - len(raw_status))
        
    colored_status = (
        f"\033[48;5;236m"
        f"\033[38;5;220m⚡ {model}\033[0m\033[48;5;236m | "
        f"ctx \033[38;5;220m{ctx_text}\033[0m\033[48;5;236m | "
        f"[\033[38;5;220m{bar}\033[0m\033[48;5;236m] \033[38;5;220m{pct_text}\033[0m\033[48;5;236m | "
        f"\033[38;5;220m{time_text}\033[0m\033[48;5;236m | "
        f"⏰\033[38;5;220m{elapsed_text}\033[0m\033[48;5;236m{padding}\033[0m"
    )
    
    print(colored_status)
    print("\033[38;5;208m" + "─" * tw + "\033[0m")


def check_and_run_setup() -> bool:
    """Interactively setup provider, key, and model if not set. Returns True if setup succeeds/is already configured."""
    cfg = load()
    
    has_provider = bool(cfg.provider)
    has_key = False
    
    if has_provider:
        try:
            pc = cfg.get_provider_config()
            defn = PROVIDER_REGISTRY.get(pc.name)
            env_key = defn.env_key if defn else ""
            if pc.api_key or (env_key and os.environ.get(env_key)):
                has_key = True
        except Exception:
            pass
            
    if has_provider and has_key and cfg.model:
        return True
        
    print("\033[33m⚠️  SUDO CLI is not configured yet.\033[0m")
    try:
        choice = input("Would you like to set up an LLM provider now? (y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
        
    if choice not in ("y", "yes"):
        return False
        
    all_providers = sorted(PROVIDER_REGISTRY.keys())
    print("\nSelect a provider:")
    for idx, p_name in enumerate(all_providers, 1):
        defn = PROVIDER_REGISTRY[p_name]
        print(f"  {idx:2d}. {defn.display} ({p_name})")
        
    try:
        sel = input(f"\nChoose option (1-{len(all_providers)}) or enter provider name: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
        
    selected_name = ""
    if sel.isdigit() and 1 <= int(sel) <= len(all_providers):
        selected_name = all_providers[int(sel)-1]
    elif sel in PROVIDER_REGISTRY:
        selected_name = sel
    else:
        print("\033[31mInvalid selection.\033[0m")
        return False
        
    defn = PROVIDER_REGISTRY[selected_name]
    print(f"\nSetting up provider: \033[1m{defn.display}\033[0m ({selected_name})")
    print(f"  Get API key at: {defn.docs_url}")
    print(f"  You can set environment variable: {defn.env_key}")
    
    try:
        use_env = input(f"Use environment variable {defn.env_key}? (y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
        
    api_key = ""
    if use_env in ("y", "yes"):
        if os.environ.get(defn.env_key):
            print(f"\033[32mFound {defn.env_key} in environment.\033[0m")
        else:
            print(f"\033[33mWarning: {defn.env_key} is not set in your current environment.\033[0m")
            print(f"Make sure to export {defn.env_key}=your_key")
    else:
        try:
            api_key = input("Enter API key: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
            
        if not api_key:
            print("\033[31mAPI key cannot be empty.\033[0m")
            return False
            
    popular_models = {
        "google/gemini": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-pro-exp"],
        "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"],
        "github": ["gpt-4o", "gpt-4o-mini", "meta-llama-3.1-70b-instruct", "cohere-command-r-plus"],
        "openrouter": ["openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.0-flash-exp", "anthropic/claude-3.5-sonnet"],
        "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini", "o1-preview"],
        "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
        "deepseek": ["deepseek-chat", "deepseek-coder"],
    }
    
    suggested = popular_models.get(selected_name, [])
    selected_model = ""
    
    print("\nSelect a model:")
    if suggested:
        for idx, m in enumerate(suggested, 1):
            print(f"  {idx}. {m}")
        print(f"  {len(suggested)+1}. Custom / Enter manually")
        
        try:
            m_sel = input(f"Choose model (1-{len(suggested)+1}, default 1): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
            
        if not m_sel:
            selected_model = suggested[0]
        elif m_sel.isdigit() and 1 <= int(m_sel) <= len(suggested):
            selected_model = suggested[int(m_sel)-1]
        else:
            try:
                selected_model = input("Enter model name: ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return False
    else:
        default_m = defn.default_model if defn else "gpt-4o"
        try:
            selected_model = input(f"Enter model name (default {default_m}): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if not selected_model:
            selected_model = default_m
            
    if not selected_model:
        print("\033[31mModel name cannot be empty.\033[0m")
        return False
        
    cfg.provider = selected_name
    if api_key:
        cfg.api_key = api_key
    cfg.model = selected_model
    save(cfg)
    print("\n\033[32m✓ Configuration saved successfully!\033[0m\n")
    return True


def parse_and_execute_tools(response_text: str) -> tuple[bool, str]:
    """Parses tool calls from response_text and executes them.
    
    Returns a tuple: (had_tool_call, result_message)
    """
    # 1. Check write_file
    write_match = re.search(r'<tool:write_file\s+path=["\'](.*?)["\']\s*>(.*?)</tool:write_file>', response_text, re.DOTALL)
    if write_match:
        path = write_match.group(1).strip()
        content = write_match.group(2)
        try:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, f"[Tool Output: File written successfully to {path}]"
        except Exception as e:
            return True, f"[Tool Output Error writing file: {e}]"
            
    # 2. Check read_file
    read_match = re.search(r'<tool:read_file\s+path=["\'](.*?)["\']\s*/>', response_text)
    if read_match:
        path = read_match.group(1).strip()
        try:
            abs_path = os.path.abspath(path)
            if not os.path.exists(abs_path):
                return True, f"[Tool Output Error: File {path} does not exist]"
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(5000)
            return True, f"[Tool Output for read_file {path}]:\n{content}"
        except Exception as e:
            return True, f"[Tool Output Error reading file: {e}]"
            
    # 3. Check list_dir
    list_match = re.search(r'<tool:list_dir\s+path=["\'](.*?)["\']\s*/>', response_text)
    if list_match:
        path = list_match.group(1).strip()
        try:
            abs_path = os.path.abspath(path)
            if not os.path.exists(abs_path):
                return True, f"[Tool Output Error: Directory {path} does not exist]"
            files = os.listdir(abs_path)
            files_str = "\n".join(files)
            return True, f"[Tool Output for list_dir {path}]:\n{files_str}"
        except Exception as e:
            return True, f"[Tool Output Error listing directory: {e}]"
            
    # 4. Check delete_file
    delete_match = re.search(r'<tool:delete_file\s+path=["\'](.*?)["\']\s*/>', response_text)
    if delete_match:
        path = delete_match.group(1).strip()
        try:
            abs_path = os.path.abspath(path)
            if os.path.isdir(abs_path):
                import shutil
                shutil.rmtree(abs_path)
                return True, f"[Tool Output: Directory {path} deleted successfully]"
            elif os.path.exists(abs_path):
                os.remove(abs_path)
                return True, f"[Tool Output: File {path} deleted successfully]"
            else:
                return True, f"[Tool Output Error: Path {path} does not exist]"
        except Exception as e:
            return True, f"[Tool Output Error deleting path: {e}]"
            
    # 5. Check run_command
    cmd_match = re.search(r'<tool:run_command\s+cmd=["\'](.*?)["\']\s*/>', response_text)
    if cmd_match:
        cmd = cmd_match.group(1).strip()
        try:
            import subprocess
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            output = f"Stdout:\n{res.stdout}\nStderr:\n{res.stderr}"
            return True, f"[Tool Output for run_command '{cmd}']: (Exit Code: {res.returncode})\n{output}"
        except Exception as e:
            return True, f"[Tool Output Error executing command: {e}]"
            
    return False, ""


def run_chat(args) -> int:
    print_banner(__version__)
    
    if not check_and_run_setup():
        print("\033[31mError: Chat session cannot start without configuration.\033[0m")
        return 1
        
    cfg = load()
    try:
        pc = cfg.get_provider_config()
        provider = ProviderFactory.create(pc.name, api_key=pc.api_key, model=pc.model, base_url=pc.base_url)
    except Exception as e:
        print(f"\033[31mError: {e}\033[0m")
        return 1
        
    sm = SessionManager()
    session_data = sm.load()
    
    messages = session_data.get("chat_messages", [])
    
    # Always keep system prompt synchronized with tools description
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    else:
        messages[0]["content"] = SYSTEM_PROMPT
        
    last_response_time = -1.0
    start_time = time.time()
    
    try:
        import readline
    except ImportError:
        pass
        
    while True:
        try:
            print_status_bar(provider.model, messages, last_response_time, start_time)
            
            try:
                user_input = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting chat. Goodbye!")
                break
                
            if not user_input:
                continue
                
            if user_input.startswith("/"):
                cmd_parts = user_input.split(None, 1)
                cmd = cmd_parts[0].lower()
                cmd_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                
                if cmd in ("/exit", "/quit"):
                    print("Goodbye!")
                    break
                elif cmd == "/clear":
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    session_data["chat_messages"] = messages
                    sm.save(session_data)
                    last_response_time = -1.0
                    print("\033[32mConversation history cleared.\033[0m\n")
                    continue
                elif cmd == "/help":
                    print("Commands:")
                    print("  /model [name]  Show or change model")
                    print("  /clear         Clear conversation history")
                    print("  /help          Show this message")
                    print("  /exit, /quit   Exit chat")
                    print()
                    continue
                elif cmd == "/model":
                    if not cmd_arg:
                        print(f"Current model: \033[1m{provider.model}\033[0m")
                        print("Fetching available models from provider...")
                        try:
                            models = provider.list_models()
                            if models:
                                print("Available models:")
                                for m in models[:10]:
                                    mid = m.get("id", m.get("name", "?"))
                                    print(f"  • {mid}")
                                if len(models) > 10:
                                    print(f"  ... and {len(models) - 10} more")
                            else:
                                print("No models returned by provider.")
                        except Exception as e:
                            print(f"Error fetching models: {e}")
                        print()
                    else:
                        new_model = cmd_arg
                        cfg.model = new_model
                        save(cfg)
                        try:
                            pc = cfg.get_provider_config()
                            provider = ProviderFactory.create(pc.name, api_key=pc.api_key, model=new_model, base_url=pc.base_url)
                            print(f"\033[32mModel successfully changed to \033[1m{new_model}\033[0m\033[0m\n")
                        except Exception as e:
                            print(f"\033[31mError updating provider: {e}\033[0m\n")
                    continue
                else:
                    print(f"\033[31mUnknown command: {cmd}\033[0m\n")
                    continue
            
            # User input starts the agent turns loop
            messages.append({"role": "user", "content": user_input})
            
            response_start = time.time()
            max_turns = 10
            turn = 0
            
            while turn < max_turns:
                turn += 1
                print("\033[1mSUDO:\033[0m")
                
                current_response = ""
                try:
                    for chunk in chat_stream(provider, messages):
                        print(chunk, end="", flush=True)
                        current_response += chunk
                except Exception as e:
                    print(f"\n\033[31mError during stream: {e}\033[0m")
                    break
                    
                print()
                
                # Check for tool calls
                had_tool_call, tool_output = parse_and_execute_tools(current_response)
                
                if had_tool_call:
                    # Append assistant's turn with tool call to messages
                    messages.append({"role": "assistant", "content": current_response})
                    # Show tool execution status beautifully in terminal
                    # Truncate output line for clean terminal logs
                    clean_status = tool_output.splitlines()[0] if tool_output.strip() else ""
                    print(f"\033[36m⚙️ {clean_status[:66]}...\033[0m")
                    # Append tool response as user turn
                    messages.append({"role": "user", "content": tool_output})
                    # Save messages state
                    session_data["chat_messages"] = messages
                    sm.save(session_data)
                    # Loop back to let the assistant process output
                    continue
                else:
                    # No tool calls made, response is final
                    if current_response.strip():
                        messages.append({"role": "assistant", "content": current_response})
                        session_data["chat_messages"] = messages
                        sm.save(session_data)
                    break
                    
            print() # Print an extra newline after the agent finished task sequence
            last_response_time = time.time() - response_start
            
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
            
    return 0
