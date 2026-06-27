"""'sudo chat' command — interactive AI chat session."""

from __future__ import annotations

import os
import sys
import time
import re
import json
import base64
import mimetypes
from pathlib import Path
from typing import Generator, Any, Optional

from sudo.core.config import load, save
from sudo.core.provider import PROVIDER_REGISTRY, ProviderFactory, BaseProvider, TIER_LABELS, TIER_ORDER
from sudo.core.session import SessionManager
from sudo.core import tools
from sudo.utils.output import terminal_width
from sudo.utils.banner import print_banner
from sudo import __version__


SYSTEM_PROMPT = (
    "You are SUDO, an autonomous AI coding assistant running in Android Termux.\n\n"
    "CRITICAL CONSTRAINTS:\n"
    "- Be direct, professional, and clear. Avoid excessive greetings or unnecessary filler, but respond naturally and helpfully to the user.\n"
    "- You can explain what you are about to do before calling a tool, and explain what you did after a tool runs.\n"
    "- If the user says hello or hi, greet them back professionally and ask how you can assist them.\n\n"
    "To interact with the environment, use the tool calls described below. If you do not need to run a tool to address the user's input (e.g., for greetings, general questions, or chat), respond with a direct text answer instead of calling a tool.\n"
    "Do NOT combine multiple tool calls in a single turn. Only call one tool at a time, wait for the tool output, then decide the next action.\n\n"
    "Use XML tags to call tools:\n"
    + tools.get_system_prompt_tools() + "\n\n"
    "When you run a tool, the output of the tool will be provided to you in the next turn."
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


def load_multimodal_file(path: str) -> dict[str, str] | None:
    """Loads and base64-encodes an image or video file."""
    try:
        path = path.strip().strip('"').strip("'")
        if not os.path.exists(path):
            return None
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            # Fallback mappings
            if path.lower().endswith('.mp4'):
                mime_type = 'video/mp4'
            elif path.lower().endswith('.png'):
                mime_type = 'image/png'
            elif path.lower().endswith('.webp'):
                mime_type = 'image/webp'
            elif path.lower().endswith('.gif'):
                mime_type = 'image/gif'
            else:
                mime_type = 'image/jpeg'
                
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('utf-8')
            
        return {
            "mime_type": mime_type,
            "data": data,
            "path": path
        }
    except Exception:
        return None


def trim_context(messages: list[dict], model: str, reserved_ratio: float = 0.85) -> list[dict]:
    """Trim message list to stay within context window.

    Strategy: always keep system prompt and last 3 turns.
    Summarize older messages into a single condensed user message.
    Uses token estimation (chars/4) to decide when to trim.
    """
    ctx_limit = get_context_limit(model)
    target_max = int(ctx_limit * reserved_ratio)

    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars // 4 <= target_max:
        return messages

    # Always keep system prompt (index 0) and last 3 exchanges
    trimmed = messages[:1]  # system prompt
    if len(messages) > 7:
        trimmed.append({"role": "user", "content": "[Earlier conversation history was trimmed to fit context window. Key context retained below.]"})
    trimmed.extend(messages[-6:] if len(messages) > 1 else messages[1:])
    return trimmed


def chat_stream(provider: BaseProvider, messages: list[dict], usage_stats: dict[str, int], **kwargs) -> Generator[str, None, None]:
    """Stream chat responses, converting messages to multimodal payloads if attachments are present."""
    api_type = provider.defn.api_type
    full_response = ""
    
    try:
        import httpx
        
        if api_type == "openai":
            openai_messages = []
            for m in messages:
                role = m["role"]
                content = m["content"]
                attachments = m.get("attachments", [])
                if attachments:
                    content_blocks = [{"type": "text", "text": content}]
                    for att in attachments:
                        if "image" in att["mime_type"]:
                            content_blocks.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{att['mime_type']};base64,{att['data']}"
                                }
                            })
                    openai_messages.append({"role": role, "content": content_blocks})
                else:
                    openai_messages.append({"role": role, "content": content})
                    
            body = {"model": provider.model, "messages": openai_messages, "stream": True, "stream_options": {"include_usage": True}, **kwargs}
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
                            usage = data.get("usage")
                            if usage:
                                usage_stats["prompt_tokens"] = usage.get("prompt_tokens", 0)
                                usage_stats["completion_tokens"] = usage.get("completion_tokens", 0)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_response += content
                                    yield content
                        except Exception:
                            pass
                            
        elif api_type == "anthropic":
            anthropic_messages = []
            system_msg = None
            for m in messages:
                if m["role"] == "system":
                    system_msg = m["content"]
                    continue
                content = m["content"]
                attachments = m.get("attachments", [])
                if attachments:
                    content_blocks = [{"type": "text", "text": content}]
                    for att in attachments:
                        if "image" in att["mime_type"]:
                            content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": att["mime_type"],
                                    "data": att["data"]
                                }
                            })
                    anthropic_messages.append({"role": m["role"], "content": content_blocks})
                else:
                    anthropic_messages.append({"role": m["role"], "content": content})
                    
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
                        try:
                            data = json.loads(data_str)
                            if event_name == "message_start":
                                usage = data.get("message", {}).get("usage", {})
                                usage_stats["prompt_tokens"] = usage.get("input_tokens", 0)
                            elif event_name == "message_delta":
                                usage = data.get("usage", {})
                                usage_stats["completion_tokens"] = usage.get("output_tokens", 0)
                            elif event_name == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    full_response += text
                                    yield text
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
                parts = [{"text": m["content"]}]
                for att in m.get("attachments", []):
                    parts.append({
                        "inline_data": {
                            "mime_type": att["mime_type"],
                            "data": att["data"]
                        }
                    })
                gemini_contents.append({"role": role, "parts": parts})
                
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
                    # Parse usage metadata if returned inside stream
                    try:
                        matches_usage = re.findall(r'"usageMetadata"\s*:\s*(\{.*?\})', buffer, re.DOTALL)
                        if matches_usage:
                            meta = json.loads(matches_usage[-1])
                            usage_stats["prompt_tokens"] = meta.get("promptTokenCount", 0)
                            usage_stats["completion_tokens"] = meta.get("candidatesTokenCount", 0)
                    except Exception:
                        pass
                        
                    matches = list(re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', buffer))
                    if matches:
                        for match in matches:
                            text_val = match.group(1)
                            try:
                                text_val = json.loads(f'"{text_val}"')
                            except Exception:
                                pass
                            full_response += text_val
                            yield text_val
                        buffer = buffer[matches[-1].end():]
        else:
            res = provider.chat(messages, **kwargs)
            text = extract_content(res, api_type)
            full_response += text
            yield text
            
    except Exception as e:
        # Fallback to non-stream call inside generator if stream errors out
        try:
            res = provider.chat(messages, **kwargs)
            text = extract_content(res, api_type)
            usage = parse_usage(res, api_type)
            usage_stats["prompt_tokens"] = usage[0]
            usage_stats["completion_tokens"] = usage[1]
            full_response += text
            yield text
        except Exception as inner_e:
            yield f"\n[Streaming error: {e}. Fallback error: {inner_e}]"
            
    # Standard fallback token estimation if provider returns 0 tokens
    if usage_stats.get("prompt_tokens", 0) == 0:
        total_chars = sum(len(m["content"]) for m in messages)
        usage_stats["prompt_tokens"] = total_chars // 4
    if usage_stats.get("completion_tokens", 0) == 0:
        usage_stats["completion_tokens"] = len(full_response) // 4


def parse_usage(response: dict, api_type: str) -> tuple[int, int]:
    p_tok, c_tok = 0, 0
    try:
        if api_type == "google":
            meta = response.get("usageMetadata", {})
            p_tok = meta.get("promptTokenCount", 0)
            c_tok = meta.get("candidatesTokenCount", 0)
        elif api_type == "anthropic":
            usage = response.get("usage", {})
            p_tok = usage.get("input_tokens", 0)
            c_tok = usage.get("output_tokens", 0)
        else:
            usage = response.get("usage", {})
            p_tok = usage.get("prompt_tokens", 0)
            c_tok = usage.get("completion_tokens", 0)
    except Exception:
        pass
    return p_tok, c_tok


def print_status_bar(model: str, messages: list[dict], last_response_time: float, start_time: float) -> None:
    tw = terminal_width()
    
    total_chars = sum(len(m["content"]) for m in messages)
    tokens = total_chars // 4
    
    # Truncate model name to save visual space for the progress bar
    display_model = model
    if len(display_model) > 16:
        display_model = display_model[:13] + "..."
        
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
        time_text = "0s"
    else:
        time_text = f"{int(round(last_response_time))}s"
        
    elapsed = int(time.time() - start_time)
    if elapsed >= 60:
        elapsed_text = f"{elapsed // 60}m"
    else:
        elapsed_text = f"{elapsed}s"
        
    def visual_length(s: str) -> int:
        length = 0
        for char in s:
            if char in ("⚡", "⏰", "⚕"):
                length += 2
            else:
                length += 1
        return length

    fixed_v_len = visual_length(f"⚡ {display_model} | ctx {ctx_text} | [] {pct_text} | {time_text} | ⏰{elapsed_text}")
    available_bar_space = tw - fixed_v_len - 2
    bar_width = max(6, min(15, available_bar_space))
    
    num_filled = min(bar_width, int((bar_pct / 100) * bar_width))
    num_empty = bar_width - num_filled
    bar = "█" * num_filled + "░" * num_empty
    
    raw_status = f"⚡ {display_model} | ctx {ctx_text} | [{bar}] {pct_text} | {time_text} | ⏰{elapsed_text}"
    v_len = visual_length(raw_status)
    
    if v_len > tw:
        padding = ""
    else:
        padding = " " * (tw - v_len)
        
    colored_status = (
        f"\033[48;5;236m"
        f"\033[38;5;220m⚡ {display_model}\033[0m\033[48;5;236m | "
        f"ctx \033[38;5;220m{ctx_text}\033[0m\033[48;5;236m | "
        f"[\033[38;5;220m{'█' * num_filled}\033[0m\033[38;5;246m{'░' * num_empty}\033[0m\033[48;5;236m] \033[38;5;220m{pct_text}\033[0m\033[48;5;236m | "
        f"\033[38;5;220m{time_text}\033[0m\033[48;5;236m | "
        f"⏰\033[38;5;220m{elapsed_text}\033[0m\033[48;5;236m{padding}\033[0m"
    )
    
    print("\033[38;5;208m" + "─" * tw + "\033[0m")
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
    print("\n\033[32m✓ Configuration saved successfully!\033[0m")
    try:
        provider = ProviderFactory.create(selected_name, api_key=api_key or os.environ.get(defn.env_key), model=selected_model)
        status = check_key_billing_status(provider)
        print(f"  Key Type Detected: \033[32m{status}\033[0m")
    except Exception:
        pass
    print()
    return True


# parse_and_execute_tools moved to sudo.core.tools


# ── Chat Session Persistence Helpers ─────────────────────────────────────────

def get_sessions_dir() -> Path:
    sm = SessionManager()
    d = sm.state_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_active_session_id() -> str:
    sm = SessionManager()
    data = sm.load()
    if "active_session_id" not in data:
        data["active_session_id"] = f"session_{int(time.time())}"
        sm.save(data)
    return data["active_session_id"]


def load_active_session_messages(session_id: str) -> list[dict]:
    s_dir = get_sessions_dir()
    s_file = s_dir / f"{session_id}.json"
    if s_file.exists():
        try:
            with open(s_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("messages", [])
        except Exception:
            pass
    return []


def save_active_session_messages(session_id: str, messages: list[dict]) -> None:
    s_dir = get_sessions_dir()
    s_file = s_dir / f"{session_id}.json"
    try:
        with open(s_file, 'w', encoding='utf-8') as f:
            json.dump({"messages": messages, "timestamp": time.time()}, f, indent=2)
    except Exception:
        pass


def handle_sessions_cmd(cmd_arg: str, session_data: dict, sm: SessionManager) -> tuple[str, list[dict]]:
    """Handles sessions commands. Returns (active_session_id, messages)."""
    s_dir = get_sessions_dir()
    active_session_id = session_data.get("active_session_id", get_active_session_id())
    
    def _safe_mtime(f):
        try:
            return f.stat().st_mtime
        except OSError:
            return 0
    files = sorted(s_dir.glob("session_*.json"), key=_safe_mtime)
    
    if not cmd_arg:
        print("\n\033[1mSaved Chat Sessions:\033[0m")
        if not files:
            print("  (no saved sessions)")
        else:
            for idx, f in enumerate(files, 1):
                s_id = f.stem
                is_active = " \033[32m[Active]\033[0m" if s_id == active_session_id else ""
                try:
                    with open(f, 'r', encoding='utf-8') as sf:
                        s_data = json.load(sf)
                        msgs = s_data.get("messages", [])
                except Exception:
                    msgs = []
                first_query = ""
                for m in msgs:
                    if m["role"] == "user":
                        first_query = m["content"]
                        break
                summary = first_query[:40] + "..." if len(first_query) > 40 else (first_query or "(empty session)")
                m_count = len(msgs)
                mtime = time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))
                print(f"  {idx:2d}. {mtime} | {m_count:2d} msgs | {summary}{is_active}")
        print("\nCommands:")
        print("  /sessions load <num>       Load a session")
        print("  /sessions new              Start a new session")
        print("  /sessions delete <num>     Delete a session")
        print("  /sessions export <num|all> Export session(s) as JSON")
        print("  /sessions import <path>    Import session from JSON file")
        print("  /sessions cleanup          Remove sessions older than 30 days")
        print()
        return active_session_id, load_active_session_messages(active_session_id)
        
    cmd_parts = cmd_arg.split(None, 1)
    sub = cmd_parts[0].lower()
    sub_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
    
    if sub == "new":
        active_session_id = f"session_{int(time.time())}"
        session_data["active_session_id"] = active_session_id
        sm.save(session_data)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        save_active_session_messages(active_session_id, messages)
        print(f"\033[32mStarted new session: {active_session_id}\033[0m\n")
        return active_session_id, messages
        
    elif sub == "load":
        if not sub_arg.isdigit():
            print("\033[31mError: Provide a valid session number.\033[0m\n")
            return active_session_id, load_active_session_messages(active_session_id)
        idx = int(sub_arg)
        if 1 <= idx <= len(files):
            active_session_id = files[idx-1].stem
            session_data["active_session_id"] = active_session_id
            sm.save(session_data)
            messages = load_active_session_messages(active_session_id)
            print(f"\033[32mLoaded session: {active_session_id}\033[0m\n")
            return active_session_id, messages
        else:
            print("\033[31mError: Session index out of range.\033[0m\n")
            
    elif sub == "delete":
        if not sub_arg.isdigit():
            print("\033[31mError: Provide a valid session number to delete.\033[0m\n")
            return active_session_id, load_active_session_messages(active_session_id)
        idx = int(sub_arg)
        if 1 <= idx <= len(files):
            target_file = files[idx-1]
            target_id = target_file.stem
            try:
                os.remove(target_file)
                print(f"\033[32mDeleted session: {target_id}\033[0m")
            except Exception as e:
                print(f"\033[31mError deleting session: {e}\033[0m")
            if target_id == active_session_id:
                remaining_files = sorted(s_dir.glob("session_*.json"), key=_safe_mtime)
                if remaining_files:
                    active_session_id = remaining_files[-1].stem
                else:
                    active_session_id = f"session_{int(time.time())}"
                session_data["active_session_id"] = active_session_id
                sm.save(session_data)
                messages = load_active_session_messages(active_session_id)
                if not messages:
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    save_active_session_messages(active_session_id, messages)
                print(f"\033[32mSwitched active session to: {active_session_id}\033[0m\n")
                return active_session_id, messages
            print()
        else:
            print("\033[31mError: Session index out of range.\033[0m\n")
    elif sub == "export":
        dest = sub_arg.lower() if sub_arg else ""
        if dest == "all":
            export_data = {}
            for f in files:
                s_id = f.stem
                try:
                    export_data[s_id] = json.loads(f.read_text())
                except Exception:
                    pass
            export_path = s_dir.parent / "sessions_export.json"
            try:
                export_path.write_text(json.dumps(export_data, indent=2))
                print(f"\033[32mExported {len(export_data)} sessions to {export_path}\033[0m\n")
            except Exception as e:
                print(f"\033[31mExport error: {e}\033[0m\n")
        elif dest.isdigit():
            idx = int(dest)
            if 1 <= idx <= len(files):
                target = files[idx - 1]
                export_path = s_dir.parent / f"{target.stem}_export.json"
                try:
                    export_path.write_text(json.dumps(json.loads(target.read_text()), indent=2))
                    print(f"\033[32mExported session to {export_path}\033[0m\n")
                except Exception as e:
                    print(f"\033[31mExport error: {e}\033[0m\n")
            else:
                print("\033[31mError: Session index out of range.\033[0m\n")
        else:
            # Export active session
            export_path = s_dir.parent / f"{active_session_id}_export.json"
            try:
                msg_data = {"messages": messages, "timestamp": time.time()}
                export_path.write_text(json.dumps(msg_data, indent=2))
                print(f"\033[32mExported active session to {export_path}\033[0m\n")
            except Exception as e:
                print(f"\033[31mExport error: {e}\033[0m\n")
        return active_session_id, messages

    elif sub == "import":
        if not sub_arg:
            print("\033[31mError: Provide a path to a session JSON file.\033[0m\n")
            return active_session_id, load_active_session_messages(active_session_id)
        import_path = Path(sub_arg)
        if not import_path.exists():
            print(f"\033[31mError: File {sub_arg} not found.\033[0m\n")
            return active_session_id, load_active_session_messages(active_session_id)
        try:
            import_data = json.loads(import_path.read_text())
            imported_msgs = import_data.get("messages", [])
            if not imported_msgs:
                print("\033[31mError: No messages found in import file.\033[0m\n")
                return active_session_id, load_active_session_messages(active_session_id)
            new_id = f"imported_{int(time.time())}"
            save_active_session_messages(new_id, imported_msgs)
            active_session_id = new_id
            session_data["active_session_id"] = active_session_id
            sm.save(session_data)
            messages = imported_msgs
            print(f"\033[32mImported session ({len(imported_msgs)} messages) as {new_id}\033[0m\n")
        except Exception as e:
            print(f"\033[31mImport error: {e}\033[0m\n")
        return active_session_id, messages

    elif sub == "cleanup":
        now = time.time()
        cutoff = now - (30 * 24 * 3600)  # 30 days
        removed = 0
        for f in list(files):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            print(f"\033[32mCleaned up {removed} session(s) older than 30 days.\033[0m\n")
        else:
            print("No sessions older than 30 days found.\n")
        return active_session_id, load_active_session_messages(active_session_id)

    else:
        print(f"\033[31mUnknown sessions subcommand: {sub}\033[0m\n")

    return active_session_id, load_active_session_messages(active_session_id)


def add_cumulative_usage(prompt_tokens: int, completion_tokens: int) -> None:
    sm = SessionManager()
    data = sm.load()
    usage = data.setdefault("cumulative_usage", {"prompt_tokens": 0, "completion_tokens": 0})
    usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + prompt_tokens
    usage["completion_tokens"] = usage.get("completion_tokens", 0) + completion_tokens
    sm.save(data)


def check_key_billing_status(provider: BaseProvider) -> str:
    """Detects if the configured API key is on a free tier or a paid subscription."""
    name = provider.defn.name
    api_key = provider.api_key
    
    # 1. OpenRouter key details check
    if name == "openrouter":
        try:
            import httpx
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = httpx.get("https://openrouter.ai/api/v1/auth/key", headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                is_free = data.get("data", {}).get("is_free_tier", False)
                limit = data.get("data", {}).get("limit", 0)
                usage = data.get("data", {}).get("usage", 0)
                if not is_free:
                    return f"Paid Key (Limit: ${limit:.2f}, Usage: ${usage:.4f})"
                return "Free Tier Key"
        except Exception:
            pass
            
    # 2. DeepSeek balance check
    elif name == "deepseek":
        try:
            import httpx
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = httpx.get("https://api.deepseek.com/user/balance", headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("is_available"):
                    infos = data.get("balance_infos", [])
                    if infos:
                        total = infos[0].get("total_balance", "0")
                        return f"Paid Key (Balance: {total} {infos[0].get('currency', 'USD')})"
        except Exception:
            pass
            
    # 3. Fallback based on registry metadata tier
    if provider.defn.free_tier:
        return "Free Tier Provider Key"
    else:
        return "Paid Tier Provider Key"


def handle_usage_cmd(session_prompt_tokens: int, session_completion_tokens: int, provider: BaseProvider) -> None:
    sm = SessionManager()
    data = sm.load()
    usage = data.get("cumulative_usage", {"prompt_tokens": 0, "completion_tokens": 0})
    cum_p = usage.get("prompt_tokens", 0)
    cum_c = usage.get("completion_tokens", 0)
    
    session_cost = (session_prompt_tokens * 0.15 + session_completion_tokens * 0.60) / 1000000
    cumulative_cost = (cum_p * 0.15 + cum_c * 0.60) / 1000000
    
    billing_status = check_key_billing_status(provider)
    
    print("\n\033[1mLLM Token Usage & Cost Status:\033[0m")
    print(f"  \033[1mKey Type:\033[0m             {billing_status}")
    print("  \033[1mSession:\033[0m")
    print(f"    Prompt Tokens:      {session_prompt_tokens:,}")
    print(f"    Completion Tokens:  {session_completion_tokens:,}")
    print(f"    Total Tokens:       {session_prompt_tokens + session_completion_tokens:,}")
    print(f"    Estimated Cost:     ${session_cost:.6f}")
    print("  \033[1mCumulative (Project):\033[0m")
    print(f"    Prompt Tokens:      {cum_p:,}")
    print(f"    Completion Tokens:  {cum_c:,}")
    print(f"    Total Tokens:       {cum_p + cum_c:,}")
    print(f"    Estimated Cost:     ${cumulative_cost:.6f}")
    print()


# ── Main Session Execution Loop ──────────────────────────────────────────────

def run_chat(args) -> int:
    quiet = getattr(args, "quiet", False)
    pipe_input = getattr(args, "pipe_input", None)
    json_output = getattr(args, "json_output", False)

    if not quiet:
        print_banner(__version__)
    if not check_and_run_setup():
        if not quiet:
            print("\033[31mError: Chat session cannot start without configuration.\033[0m")
        return 1

    cfg = load()
    try:
        pc = cfg.get_provider_config()
        provider = ProviderFactory.create(pc.name, api_key=pc.api_key, model=pc.model, base_url=pc.base_url)
    except Exception as e:
        if json_output:
            print(json.dumps({"error": str(e)}))
        elif not quiet:
            print(f"\033[31mError: {e}\033[0m")
        return 1
        
    from sudo.core.plugins import run_hooks

    sm = SessionManager()
    session_data = sm.load()
    run_hooks("on_chat_start", cfg, provider)
    
    # Load or initialize the active session and messages
    active_session_id = session_data.get("active_session_id") or get_active_session_id()
    session_data["active_session_id"] = active_session_id
    sm.save(session_data)
    
    messages = load_active_session_messages(active_session_id)
    if not messages:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        save_active_session_messages(active_session_id, messages)
        
    # Synchronize system prompt tools definitions
    messages[0]["content"] = SYSTEM_PROMPT
    
    last_response_time = -1.0
    start_time = time.time()
    
    # Current session tokens tracking
    session_prompt_tokens = 0
    session_completion_tokens = 0
    
    # Session attachments staging
    current_attachments: list[dict[str, str]] = []
    
    try:
        import readline
    except ImportError:
        pass

    # Handle pipe mode — process initial input non-interactively
    if pipe_input and not sys.stdin.isatty():
        user_msg = {"role": "user", "content": pipe_input}
        messages.append(user_msg)

        response_start = time.time()
        current_response = ""
        usage_stats = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            messages = trim_context(messages, provider.model)
            for chunk in chat_stream(provider, messages, usage_stats=usage_stats):
                current_response += chunk
                if not quiet:
                    print(chunk, end="", flush=True)
        except Exception as e:
            if json_output:
                print(json.dumps({"error": str(e)}))
            elif not quiet:
                print(f"\n\033[31mError: {e}\033[0m")

        if current_response.strip():
            messages.append({"role": "assistant", "content": current_response})

        session_prompt_tokens += usage_stats["prompt_tokens"]
        session_completion_tokens += usage_stats["completion_tokens"]
        add_cumulative_usage(usage_stats["prompt_tokens"], usage_stats["completion_tokens"])

        if json_output:
            print(json.dumps({
                "response": current_response,
                "usage": usage_stats,
            }))
        else:
            print()

        save_active_session_messages(active_session_id, messages)

        # In pure pipe mode (not interactive), exit after processing
        if not sys.stdin.isatty() and not quiet:
            return 0

    while True:
        try:
            if not quiet:
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
                    save_active_session_messages(active_session_id, messages)
                    last_response_time = -1.0
                    current_attachments.clear()
                    print("\033[32mConversation history cleared.\033[0m\n")
                    continue
                elif cmd == "/help":
                    print("Commands:")
                    print("  /connect [provider] [model]  Switch provider and/or model")
                    print("  /model [name]    Show or change model")
                    print("  /clear           Clear conversation history")
                    print("  /sessions [cmd]  Manage sessions (load, new, delete)")
                    print("  /usage           Show token usage and cost stats")
                    print("  /paste <path>    Attach an image/video to the next message")
                    print("  /help            Show this message")
                    print("  /exit, /quit     Exit chat")
                    print()
                    continue
                elif cmd == "/sessions":
                    active_session_id, messages = handle_sessions_cmd(cmd_arg, session_data, sm)
                    continue
                elif cmd == "/usage":
                    handle_usage_cmd(session_prompt_tokens, session_completion_tokens, provider)
                    continue
                elif cmd == "/paste":
                    if not cmd_arg:
                        print("\033[31mError: Provide a valid file path. Usage: /paste <path_to_file>\033[0m\n")
                        continue
                    attachment = load_multimodal_file(cmd_arg)
                    if attachment:
                        current_attachments.append(attachment)
                        print(f"\033[32m📎 Attached {attachment['path']} ({attachment['mime_type']})\033[0m\n")
                    else:
                        print(f"\033[31mError: Could not load or find file '{cmd_arg}'\033[0m\n")
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
                elif cmd == "/connect":
                    parts = cmd_arg.split(None, 1) if cmd_arg else []
                    target_provider = parts[0].strip() if parts else ""
                    target_model = parts[1].strip() if len(parts) > 1 else ""

                    if not target_provider:
                        print(f"Current provider: \033[1m{cfg.provider or '(none)'}\033[0m")
                        print(f"Current model:    \033[1m{provider.model}\033[0m")
                        print(f"\nAvailable providers ({len(PROVIDER_REGISTRY)}):")
                        for tier in TIER_ORDER:
                            provs = [(n, d) for n, d in PROVIDER_REGISTRY.items() if d.tier == tier]
                            if not provs:
                                continue
                            print(f"  {TIER_LABELS.get(tier, f'Tier {tier}')}")
                            for name, defn in sorted(provs, key=lambda x: x[0]):
                                active = " \033[32m[active]\033[0m" if name == cfg.provider else ""
                                print(f"    • {name:25s} {defn.display}{active}")
                        print()
                        print("Usage: /connect <provider> [model]")
                        print("       /connect <provider>  — switch provider, select model interactively")
                        print("       /connect <provider> <model>  — switch provider and model directly")
                        continue

                    if target_provider not in PROVIDER_REGISTRY:
                        print(f"\033[31mUnknown provider '{target_provider}'. Use /connect to list providers.\033[0m\n")
                        continue

                    defn = PROVIDER_REGISTRY[target_provider]
                    resolved_key = cfg.api_key or os.environ.get(defn.env_key)

                    if not target_model:
                        print(f"Switching to provider: \033[1m{defn.display}\033[0m ({target_provider})")
                        print(f"  Default model: {defn.default_model}")
                        suggested = {
                            "google/gemini": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
                            "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
                            "github": ["gpt-4o", "gpt-4o-mini", "meta-llama-3.1-70b-instruct"],
                            "openrouter": ["openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.0-flash-exp"],
                            "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini"],
                            "anthropic": ["claude-sonnet-4-20250514", "claude-3-5-haiku-latest"],
                            "deepseek": ["deepseek-chat", "deepseek-coder"],
                        }.get(target_provider, [defn.default_model])
                        print("  Suggested models:")
                        for i, m in enumerate(suggested, 1):
                            print(f"    {i}. {m}")
                        try:
                            sel = input(f"  Select model (1-{len(suggested)}, default 1): ").strip()
                        except (KeyboardInterrupt, EOFError):
                            print()
                            continue
                        if not sel:
                            target_model = suggested[0]
                        elif sel.isdigit() and 1 <= int(sel) <= len(suggested):
                            target_model = suggested[int(sel) - 1]
                        else:
                            target_model = sel

                    try:
                        cfg.provider = target_provider
                        cfg.model = target_model
                        save(cfg)
                        pc = cfg.get_provider_config()
                        provider = ProviderFactory.create(pc.name, api_key=pc.api_key or resolved_key, model=target_model, base_url=pc.base_url)
                        print(f"\033[32mConnected to \033[1m{defn.display}\033[0m (\033[1m{target_model}\033[0m)\033[0m\n")
                    except Exception as e:
                        print(f"\033[31mError connecting to '{target_provider}': {e}\033[0m\n")
                    continue
                else:
                    print(f"\033[31mUnknown command: {cmd}\033[0m\n")
                    continue
            
            # Scrape prompt text for any file paths ending in popular image/video extensions
            detected_paths = re.findall(r'(?:[a-zA-Z]:[\\/]|[\\/])?[\w\-.\\/]+\.(?:png|jpe?g|webp|gif|mp4|mov|avi|mkv)', user_input)
            for path in detected_paths:
                if os.path.exists(path):
                    # Attach if not already staged
                    if not any(att["path"] == path for att in current_attachments):
                        attachment = load_multimodal_file(path)
                        if attachment:
                            current_attachments.append(attachment)
                            print(f"\033[32m📎 Auto-attached detected file: {path} ({attachment['mime_type']})\033[0m")
                            
            # Add message with staged attachments
            user_msg = {"role": "user", "content": user_input}
            if current_attachments:
                user_msg["attachments"] = list(current_attachments)
                current_attachments.clear()
            messages.append(user_msg)
            
            response_start = time.time()
            max_turns = 10
            turn = 0
            
            while turn < max_turns:
                turn += 1
                tw = terminal_width()
                print(f"\033[38;5;208m─  ⚡ SUDO  " + "─" * (tw - 12) + "\033[0m")
                
                current_response = ""
                usage_stats = {"prompt_tokens": 0, "completion_tokens": 0}
                # Trim context if approaching token limit
                messages = trim_context(messages, provider.model)
                try:
                    is_start_of_line = True
                    for chunk in chat_stream(provider, messages, usage_stats=usage_stats):
                        for char in chunk:
                            if is_start_of_line:
                                if char != "\n":
                                    print("   ", end="", flush=True)
                                    is_start_of_line = False
                            if char == "\n":
                                is_start_of_line = True
                            print(char, end="", flush=True)
                        current_response += chunk
                except Exception as e:
                    print(f"\n\033[31mError during stream: {e}\033[0m")
                    break
                    
                print()
                
                # Accumulate tokens
                session_prompt_tokens += usage_stats["prompt_tokens"]
                session_completion_tokens += usage_stats["completion_tokens"]
                add_cumulative_usage(usage_stats["prompt_tokens"], usage_stats["completion_tokens"])
                
                calls = tools.parse_tool_calls(current_response)
                if calls:
                    for c in calls:
                        run_hooks("on_tool_before", c.get("name"), c.get("arguments", {}))

                had_tool_call, tool_output = tools.parse_and_execute_tools(current_response)

                if calls:
                    for c in calls:
                        run_hooks("on_tool_after", c.get("name"), c.get("arguments", {}), tool_output)

                if had_tool_call:
                    messages.append({"role": "assistant", "content": current_response})
                    # Check if the output indicates an error/failure
                    is_error = False
                    if "error" in tool_output.lower() or "exception" in tool_output.lower():
                        is_error = True
                    else:
                        exit_code_match = re.search(r'exit code:\s*([\-0-9]+)', tool_output, re.IGNORECASE)
                        if exit_code_match and exit_code_match.group(1) != "0":
                            is_error = True
                            
                    if is_error:
                        print(f"\033[31m❌ {tool_output.strip()}\033[0m")
                    else:
                        clean_status = tool_output.splitlines()[0] if tool_output.strip() else ""
                        truncated = clean_status[:63] + "..." if len(clean_status) > 63 else clean_status
                        print(f"\033[36m⚙️ {truncated}\033[0m")
                    messages.append({"role": "user", "content": tool_output})
                    save_active_session_messages(active_session_id, messages)
                    continue
                else:
                    if current_response.strip():
                        messages.append({"role": "assistant", "content": current_response})
                        save_active_session_messages(active_session_id, messages)
                    break
                    
            print()
            last_response_time = time.time() - response_start
            
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
            
    return 0
