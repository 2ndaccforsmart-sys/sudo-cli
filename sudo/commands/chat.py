"""'sudo chat' command — interactive AI chat session."""

from __future__ import annotations

import os
import sys
import time
import re
import json
from typing import Generator, Any

from sudo.core.config import load, save
from sudo.core.provider import ProviderFactory, BaseProvider
from sudo.core.session import SessionManager
from sudo.utils.output import terminal_width


SYSTEM_PROMPT = (
    "You are SUDO, a helpful AI coding assistant running in Android Termux. "
    "Provide clear, concise answers optimized for reading on mobile/terminals. "
    "Always use markdown formatting and highlight code blocks clearly."
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
        
    num_filled = min(15, int((bar_pct / 100) * 15))
    num_empty = 15 - num_filled
    bar = "█" * num_filled + "░" * num_empty
    
    if last_response_time < 0:
        time_text = "--s"
    else:
        time_text = f"{int(round(last_response_time))}s"
        
    elapsed = int(time.time() - start_time)
    if elapsed >= 60:
        elapsed_text = f"{elapsed // 60}m"
    else:
        elapsed_text = f"{elapsed}s"
        
    raw_status = f" ⚡ {model} | ctx {ctx_text} | [{bar}] {pct_text} | {time_text} | ⏰{elapsed_text} "
    
    if len(raw_status) > tw:
        raw_status = raw_status[:tw]
        padding = ""
    else:
        padding = " " * (tw - len(raw_status))
        
    colored_status = (
        f"\033[48;5;236m "
        f"\033[38;5;39m⚡\033[38;5;220m {model}\033[0m\033[48;5;236m | "
        f"ctx \033[38;5;220m{ctx_text}\033[0m\033[48;5;236m | "
        f"[\033[38;5;220m{bar}\033[0m\033[48;5;236m] \033[38;5;220m{pct_text}\033[0m\033[48;5;236m | "
        f"\033[38;5;220m{time_text}\033[0m\033[48;5;236m | "
        f"⏰\033[38;5;220m{elapsed_text}\033[0m\033[48;5;236m{padding}\033[0m"
    )
    
    print(colored_status)
    print("\033[38;5;208m" + "─" * tw + "\033[0m")


def run_chat(args) -> int:
    cfg = load()
    if not cfg.provider:
        print("\033[31mError: No provider configured.\033[0m")
        print("Use 'sudo provider set <name>' to set an active provider.")
        print("Use 'sudo provider list' to see all available providers.")
        return 1
        
    try:
        pc = cfg.get_provider_config()
        provider = ProviderFactory.create(pc.name, api_key=pc.api_key, model=pc.model, base_url=pc.base_url)
    except Exception as e:
        print(f"\033[31mError: {e}\033[0m")
        return 1
        
    print(f"Starting chat session with \033[1m{provider.defn.display}\033[0m.")
    print("Commands:")
    print("  /model [name]  Show or change model")
    print("  /clear         Clear conversation history")
    print("  /help          Show this message")
    print("  /exit, /quit   Exit chat")
    print()
    
    sm = SessionManager()
    session_data = sm.load()
    
    messages = session_data.get("chat_messages", [])
    if not any(m["role"] == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        
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
            
            messages.append({"role": "user", "content": user_input})
            print("\033[1mSUDO:\033[0m")
            
            response_start = time.time()
            full_response = ""
            try:
                for chunk in chat_stream(provider, messages):
                    print(chunk, end="", flush=True)
                    full_response += chunk
            except Exception as e:
                print(f"\n\033[31mError during stream: {e}\033[0m")
                
            print()
            print()
            
            last_response_time = time.time() - response_start
            
            if full_response.strip():
                messages.append({"role": "assistant", "content": full_response})
                session_data["chat_messages"] = messages
                sm.save(session_data)
                
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
            
    return 0
