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
        
    popular = [
        ("google/gemini", "Google Gemini (Free tier available)"),
        ("groq", "Groq (Free tier available)"),
        ("github", "GitHub Models (Free with Copilot subscription)"),
        ("openrouter", "OpenRouter (Multi-model hub with free models)"),
        ("openai", "OpenAI (GPT-4o, etc.)"),
        ("anthropic", "Anthropic (Claude Sonnet, etc.)"),
        ("deepseek", "DeepSeek (Chat & Coder)"),
    ]
    
    print("\nSelect a provider:")
    for idx, (name, desc) in enumerate(popular, 1):
        print(f"  {idx}. {desc} [{name}]")
    print("  8. Custom / Other provider")
    
    try:
        sel = input("Choose option (1-8): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
        
    selected_name = ""
    if sel.isdigit() and 1 <= int(sel) <= 7:
        selected_name = popular[int(sel)-1][0]
    elif sel == "8":
        try:
            selected_name = input("Enter provider name (e.g. ollama, together): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
    else:
        print("\033[31mInvalid selection.\033[0m")
        return False
        
    if selected_name not in PROVIDER_REGISTRY:
        print(f"\033[31mProvider '{selected_name}' is not in the registry.\033[0m")
        print("Please check available providers using 'sudo provider list'.")
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
            
    # Now prompt to select a model
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


def run_chat(args) -> int:
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
