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

_IS_UNIX = not sys.platform.startswith("win")
if _IS_UNIX:
    import tty
    import termios
else:
    import msvcrt

from sudo.core.config import load, save
from sudo.core.provider import PROVIDER_REGISTRY, ProviderFactory, BaseProvider, TIER_LABELS, TIER_ORDER
from sudo.core.session import SessionManager
from sudo.core import tools
from sudo.utils.output import terminal_width
from sudo.utils.banner import print_banner
from sudo.utils.constants import estimate_model_cost
from sudo import __version__


import threading

class SpinnerThread(threading.Thread):
    def __init__(self, initial_label: str = "Thinking..."):
        super().__init__()
        self.label = initial_label
        self.running = True
        self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]
        self.index = 0
        self.auto_cycle = False
        self.cycle_states = []
        
    def start_cycling(self, states: list[str]):
        self.auto_cycle = True
        self.cycle_states = states
        
    def run(self):
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        start_time = time.time()
        while self.running:
            frame = self.frames[self.index % len(self.frames)]
            
            if self.auto_cycle and self.cycle_states:
                elapsed = time.time() - start_time
                state_idx = int(elapsed / 1.5) % len(self.cycle_states)
                self.label = self.cycle_states[state_idx]
                
            if "[ ⠋ ]" in self.label:
                display = self.label.replace("⠋", frame)
            elif any(f in self.label for f in self.frames):
                display = self.label
                for f in self.frames:
                    display = display.replace(f, frame)
            else:
                display = f"[ {frame} ] {self.label}"
                
            sys.stdout.write(f"\r\033[2K\033[1;36m{display}\033[0m")
            sys.stdout.flush()
            self.index += 1
            time.sleep(0.08)
            
    def stop(self):
        self.running = False
        try:
            self.join()
        except RuntimeError:
            pass
        sys.stdout.write("\r\033[2K")
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def get_subjective_states(user_input: str) -> list[str]:
    ui_lower = user_input.lower()
    if any(k in ui_lower for k in ("gcs", "bucket", "cloud", "upload", "gcp")):
        return [
            "(=^・ω・^=) purring...",
            "(=^・ω・^=) kneading...",
            "[ ⠸ ] 🧬 Synthesizing...",
            "[ ⠦ ] 🛠️  Running GCS Operation..."
        ]
    if any(k in ui_lower for k in ("file", "read", "write", "patch", "create", "folder", "directory")):
        return [
            "◜(｡ •́︿•̀｡) pondering...",
            "[ ⠹ ] 📄 Reading File...",
            "[ ⠸ ] 📝 Patching File...",
            "(=^・ω・^=) kneading..."
        ]
    if any(k in ui_lower for k in ("code", "python", "refactor", "test", "pytest", "bug", "debug", "error", "syntax")):
        return [
            "[ ⠴ ] 🪲  Debugging...",
            "[ ⠼ ] ✍️  Self-Correcting...",
            "[ ⠹ ] 🔮 Analyzing...",
            "◠(⊙_⊙) contemplating..."
        ]
    if any(k in ui_lower for k in ("web", "browse", "search", "fetch", "url", "scrape", "google")):
        return [
            "[ ⠋ ] 🌐 Browsing...",
            "[ ⠙ ] 🔍 Searching...",
            "◜(｡ •́︿•̀｡) pondering..."
        ]
    return [
        "◜(｡ •́︿•̀｡) pondering...",
        "[ ⠋ ] 🧠 Thinking...",
        "◠(⊙_⊙) contemplating...",
        "[ ⠙ ] 🧐 Reflecting...",
        "[ ⠹ ] 🔮 Analyzing...",
        "[ ⠸ ] 🧬 Synthesizing...",
        "(◔_◔) pondering...",
        "[ ⠼ ] ✍️  Self-Correcting...",
        "[ 🤖 ] 💭 Internal Monologue..."
    ]


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> tags and return only visible content."""
    import re
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


def stream_filter_think_tags(stream: Generator[str, None, None]) -> Generator[str, None, None]:
    """Filter out <think>...</think> tags from a chunk stream in real-time."""
    in_think = False
    buf = ""
    open_tag = "<think>"
    close_tag = "</think>"
    
    for chunk in stream:
        buf += chunk
        while buf:
            if not in_think:
                idx = buf.find("<")
                if idx == -1:
                    yield buf
                    buf = ""
                else:
                    if idx > 0:
                        yield buf[:idx]
                        buf = buf[idx:]
                    if buf.startswith(open_tag):
                        in_think = True
                        buf = buf[len(open_tag):]
                    elif open_tag.startswith(buf):
                        break
                    else:
                        yield buf[0]
                        buf = buf[1:]
            else:
                idx = buf.find("<")
                if idx == -1:
                    buf = ""
                else:
                    buf = buf[idx:]
                    if buf.startswith(close_tag):
                        in_think = False
                        buf = buf[len(close_tag):]
                    elif close_tag.startswith(buf):
                        break
                    else:
                        buf = buf[1:]
    if buf and not in_think:
        yield buf


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
    """Loads and base64-encodes an image or video file. Max 20MB."""
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
    try:
        path = path.strip().strip('"').strip("'")
        if not os.path.exists(path):
            return None
        file_size = os.path.getsize(path)
        if file_size > MAX_FILE_SIZE:
            print(f"\033[31mError: File too large ({file_size / 1024 / 1024:.1f}MB, max 20MB).\033[0m")
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

    total_chars = sum(len(m.get("content", "")) for m in messages if m.get("role") != "system")
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
        total_chars = sum(len(m["content"]) for m in messages if m.get("role") != "system")
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


_status_bar_printed = False


def clear_previous_status_bar(user_input: str) -> None:
    global _status_bar_printed
    if _status_bar_printed:
        _status_bar_printed = False
        tw = terminal_width()
        u_lines = 0
        for line in user_input.splitlines():
            prompt_len = len(line) if u_lines > 0 else len("> " + line)
            u_lines += max(1, (prompt_len + tw - 1) // tw)
        u_lines = max(1, u_lines)
        
        move_up = u_lines + 2
        sys.stdout.write(f"\x1b[{move_up}A")
        sys.stdout.write("\x1b[2M")
        sys.stdout.write(f"\x1b[{u_lines}B")
        sys.stdout.flush()
        
    tw = terminal_width()
    print("\033[38;5;208m" + "─" * tw + "\033[0m")


def print_status_bar(model: str, messages: list[dict], last_response_time: float, start_time: float) -> None:
    global _status_bar_printed
    tw = terminal_width()

    total_chars = sum(len(m["content"]) for m in messages if m.get("role") != "system")
    tokens = total_chars // 4

    display_model = model
    if len(display_model) > 16:
        display_model = display_model[:13] + "..."
    # Sanitize: only allow safe characters
    display_model = re.sub(r"[^\w\-./:]+", "_", display_model)

    ctx_limit = get_context_limit(model)
    if ctx_limit >= 1000000:
        ctx_text = f"{ctx_limit // 1000000}M"
    elif ctx_limit >= 1000:
        ctx_text = f"{ctx_limit // 1000}k"
    else:
        ctx_text = str(ctx_limit)

    if tokens == 0:
        bar_pct = 0
        pct_text = "--"
    else:
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
            if char in ("\u26a1", "\u23f0", "\u2695"):
                length += 2
            else:
                length += 1
        return length

    fixed_v_len = visual_length(f"\u26a1 {display_model} | ctx {ctx_text} | [] {pct_text} | {time_text} | \u23f0{elapsed_text}")
    available_bar_space = tw - fixed_v_len - 2
    bar_width = max(6, min(15, available_bar_space))

    num_filled = min(bar_width, int((bar_pct / 100) * bar_width))
    num_empty = bar_width - num_filled

    raw_status = f"\u26a1 {display_model} | ctx {ctx_text} | [{'█' * num_filled}{'░' * num_empty}] {pct_text} | {time_text} | \u23f0{elapsed_text}"
    v_len = visual_length(raw_status)

    if v_len > tw:
        padding = ""
    else:
        padding = " " * (tw - v_len)

    colored_status = (
        f"\033[48;5;236m"
        f"\033[38;5;220m\u26a1 {display_model}\033[0m\033[48;5;236m | "
        f"ctx \033[38;5;220m{ctx_text}\033[0m\033[48;5;236m | "
        f"[\033[38;5;220m{'█' * num_filled}\033[0m\033[38;5;246m{'░' * num_empty}\033[0m\033[48;5;236m] \033[38;5;220m{pct_text}\033[0m\033[48;5;236m | "
        f"\033[38;5;220m{time_text}\033[0m\033[48;5;236m | "
        f"\u23f0\033[38;5;220m{elapsed_text}\033[0m\033[48;5;236m{padding}\033[0m"
    )

    print("\033[38;5;208m" + "─" * tw + "\033[0m")
    print(colored_status)
    print("\033[38;5;208m" + "─" * tw + "\033[0m")
    _status_bar_printed = True


def _visual_len(s: str) -> int:
    clean = re.sub(r'\033\[[0-9;]*m', '', s)
    length = 0
    for char in clean:
        if ord(char) > 256:
            length += 2
        else:
            length += 1
    return length


def _render_boxed_ui(title: str, lines: list[str], box_width: int = 58) -> None:
    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25l")
    title_str = f" ⚙️Model Picker - {title} "
    title_len = _visual_len(title_str)
    dash_len = box_width - title_len - 2
    if dash_len < 2:
        dash_len = 2
    top_border = f"┌─{title_str}{'─' * dash_len}┐"
    print(top_border)
    for line in lines:
        line_len = _visual_len(line)
        padding = box_width - line_len - 4
        if padding < 0:
            padding = 0
        print(f"│ {line}{' ' * padding} │")
    print(f"└{'─' * (box_width - 2)}┘")
    sys.stdout.flush()


def _render_boxed_picker(title: str, subtitle: str, options: list[str], sel: int, current: Optional[str] = None) -> None:
    box_width = 58
    lines = []
    lines.append("")
    lines.append(f"\033[1;37m{subtitle}\033[0m")
    lines.append("")
    
    import shutil
    try:
        th = shutil.get_terminal_size().lines
    except Exception:
        th = 24
    viewport_size = max(5, th - 9)
    
    top = sel - viewport_size // 2
    top = max(0, min(top, max(0, len(options) - viewport_size)))
    bottom = min(top + viewport_size, len(options))
    
    if top > 0:
        lines.append(f"\033[90m  ▲ {top} more above\033[0m")
    else:
        lines.append("")
        
    for i in range(top, bottom):
        opt = options[i]
        is_curr = False
        if current:
            is_curr = (opt == current) or (f"({current})" in opt) or (opt.split()[0] == current)
        curr_marker = "  \033[32m← current\033[0m" if is_curr else ""
        
        if i == sel:
            lines.append(f"\033[1;33m❯ {opt}\033[0m{curr_marker}")
        else:
            lines.append(f"  {opt}{curr_marker}")
            
    if bottom < len(options):
        remaining = len(options) - bottom
        lines.append(f"\033[90m  ▼ {remaining} more below\033[0m")
    else:
        lines.append("")
        
    lines.append("")
    lines.append(f"\033[90m[{sel + 1}/{len(options)}]\033[0m")
    _render_boxed_ui(title, lines, box_width)


def run_setup_wizard() -> bool:
    """Run the interactive setup wizard with beautiful boxed UI."""
    cfg = load()
    
    providers = sorted(list(PROVIDER_REGISTRY.keys()))
    options = []
    for p in providers:
        defn = PROVIDER_REGISTRY[p]
        has_key = "✓" if (cfg.api_key or os.environ.get(defn.env_key)) else ""
        key_suffix = " (key configured)" if has_key else ""
        options.append(f"{defn.display} ({p}){key_suffix}")
    options.append("Cancel")
    
    sel = 0
    if _IS_UNIX:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
    else:
        fd = None
        old = None
        
    selected_provider = None
    try:
        while True:
            curr_info = f"Current: {cfg.model or '(none)'} on {cfg.provider or '(none)'}"
            _render_boxed_picker("Select Provider", curr_info, options, sel, cfg.provider)
            
            if _IS_UNIX:
                ch = os.read(fd, 1)
            else:
                ch = msvcrt.getch()
                
            if ch in (b"\r", b"\n"):
                if sel == len(options) - 1: # Cancel
                    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                    sys.stdout.flush()
                    return False
                selected_provider = providers[sel]
                break
            if ch == b"\x03" or ch == b"q" or (ch == b"\x1b" and not _IS_UNIX):
                sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                sys.stdout.flush()
                return False
            if ch == b"\x1b" and _IS_UNIX:
                import select
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    seq = os.read(fd, 2)
                    if seq == b"[A":
                        sel = (sel - 1) % len(options)
                    elif seq == b"[B":
                        sel = (sel + 1) % len(options)
            elif ch == b"\xe0" and not _IS_UNIX:
                next_ch = msvcrt.getch()
                if next_ch == b"H":
                    sel = (sel - 1) % len(options)
                elif next_ch == b"P":
                    sel = (sel + 1) % len(options)
    finally:
        if _IS_UNIX and old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            
    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
    sys.stdout.flush()
    
    defn = PROVIDER_REGISTRY[selected_provider]
    print(f"\nSetting up provider: \033[1;32m{defn.display}\033[0m ({selected_provider})")
    print(f"  Get API key at: {defn.docs_url}")
    print(f"  Env variable:   {defn.env_key}\n")
    
    env_val = os.environ.get(defn.env_key)
    use_env = False
    if env_val:
        try:
            choice = input(f"Found {defn.env_key} in environment. Use it? (Y/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if choice not in ("n", "no"):
            use_env = True
            
    api_key = ""
    if not use_env:
        try:
            api_key = input("Enter API key (leave empty to skip and use env var later): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
            
    suggested = {
        "google/gemini": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-pro-exp"],
        "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"],
        "github": ["gpt-4o", "gpt-4o-mini", "meta-llama-3.1-70b-instruct", "cohere-command-r-plus"],
        "openrouter": ["openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.0-flash-exp", "anthropic/claude-3.5-sonnet"],
        "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini", "o1-preview"],
        "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
        "deepseek": ["deepseek-chat", "deepseek-coder"],
    }.get(selected_provider, [defn.default_model])
    
    models_list = list(suggested)
    test_key = api_key or env_val
    if test_key:
        print("Fetching model list from provider API...")
        try:
            prov_inst = ProviderFactory.create(selected_provider, api_key=test_key, model=defn.default_model)
            fetched = prov_inst.list_models()
            if fetched:
                models_list = sorted([m.get("id", m.get("name", "")) for m in fetched if m.get("id") or m.get("name")])
        except Exception:
            pass
            
    options = list(models_list) + ["Custom / Enter manually", "Cancel"]
    
    sel = 0
    if _IS_UNIX:
        tty.setraw(fd)
        
    selected_model = None
    try:
        while True:
            _render_boxed_picker(defn.display, f"Select a model ({len(models_list)} available)", options, sel, cfg.model)
            
            if _IS_UNIX:
                ch = os.read(fd, 1)
            else:
                ch = msvcrt.getch()
                
            if ch in (b"\r", b"\n"):
                if sel == len(options) - 1: # Cancel
                    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                    sys.stdout.flush()
                    return False
                if sel == len(options) - 2: # Custom
                    selected_model = "custom"
                else:
                    selected_model = options[sel]
                break
            if ch == b"\x03" or ch == b"q" or (ch == b"\x1b" and not _IS_UNIX):
                sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                sys.stdout.flush()
                return False
            if ch == b"\x1b" and _IS_UNIX:
                import select
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    seq = os.read(fd, 2)
                    if seq == b"[A":
                        sel = (sel - 1) % len(options)
                    elif seq == b"[B":
                        sel = (sel + 1) % len(options)
            elif ch == b"\xe0" and not _IS_UNIX:
                next_ch = msvcrt.getch()
                if next_ch == b"H":
                    sel = (sel - 1) % len(options)
                elif next_ch == b"P":
                    sel = (sel + 1) % len(options)
    finally:
        if _IS_UNIX and old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            
    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
    sys.stdout.flush()
    
    if selected_model == "custom":
        try:
            selected_model = input("Enter model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if not selected_model:
            print("\033[31mModel name cannot be empty.\033[0m")
            return False
            
    cfg.provider = selected_provider
    if api_key:
        cfg.api_key = api_key
    cfg.model = selected_model
    save(cfg)
    
    print("\n\033[1;32m✓ Configuration saved successfully!\033[0m")
    print(f"  Provider: \033[1m{defn.display}\033[0m")
    print(f"  Model:    \033[1m{selected_model}\033[0m\n")
    return True


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
        choice = input("Would you like to run the setup wizard now? (Y/n): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
        
    if choice in ("n", "no"):
        return False
        
    return run_setup_wizard()


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


def handle_sessions_cmd(cmd_arg: str, session_data: dict, sm: SessionManager,
                        current_messages: Optional[list[dict]] = None) -> tuple[str, list[dict]]:
    """Handles sessions commands. Returns (active_session_id, messages).

    Args:
        current_messages: The current in-memory messages list (used for export of active session).
    """
    s_dir = get_sessions_dir()
    active_session_id = session_data.get("active_session_id", get_active_session_id())
    messages = current_messages if current_messages is not None else load_active_session_messages(active_session_id)
    
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
    
    input_rate, output_rate = estimate_model_cost(provider.model)
    session_cost = (session_prompt_tokens * input_rate + session_completion_tokens * output_rate) / 1000000
    cumulative_cost = (cum_p * input_rate + cum_c * output_rate) / 1000000
    
    billing_status = check_key_billing_status(provider)
    
    print("\n\033[1mLLM Token Usage & Cost Status:\033[0m")
    print(f"  \033[1mKey Type:\033[0m             {billing_status}")
    print(f"  \033[1mModel:\033[0m               {provider.model}")
    print(f"  \033[1mRate:\033[0m                ${input_rate:.2f}/${output_rate:.2f} per 1M tokens (in/out)")
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


_COMMANDS = {
    "/connect":   "Switch provider and/or model",
    "/model":     "Pick model interactively or set directly",
    "/models":    "Pick model (alias for /model)",
    "/new":       "Start a new session",
    "/reset":     "Start a new session (alias for /new)",
    "/clear":     "Clear conversation history",
    "/sessions":  "Manage sessions",
    "/skills":    "Manage assistant behavior skills",
    "/usage":     "Show token usage and cost",
    "/paste":     "Attach an image/video",
    "/save":      "Save conversation to file",
    "/retry":     "Retry last message",
    "/undo":      "Back up N turns",
    "/title":     "Set session title",
    "/branch":    "Branch current session",
    "/fork":      "Branch (alias for /branch)",
    "/history":   "Show conversation history",
    "/redraw":    "Force full UI repaint",
    "/help":      "Show help message",
    "/exit":      "Exit chat",
    "/quit":      "Exit chat (alias for /exit)",
}


def _clear_dropdown_display(buf: str, cmds: list[str]) -> None:
    """Clear the dropdown list from the terminal view."""
    sys.stdout.write("\x1b[?25l\r\x1b[J\x1b[?25h")
    sys.stdout.flush()


def _pick_command_dropdown(prefix: str = "/") -> str | None:
    """Raw-mode dropdown for slash commands.
    Type to filter, arrows to navigate, Enter to select, Esc to cancel.
    Returns selected command string (with args) or None."""
    if _IS_UNIX:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
    else:
        fd = None
        old = None

    sel = 0
    buf = prefix
    
    try:
        from sudo.core.skills import load_skills
        skills = load_skills()
        all_cmds = dict(_COMMANDS)
        for sname, sinfo in skills.items():
            all_cmds[f"/{sname}"] = sinfo["description"]
            
        while True:
            cmds = [c for c in all_cmds if c.startswith(buf)]
            sel = min(sel, max(0, len(cmds) - 1))
            _draw_cmd_dropdown(buf, cmds, sel, all_cmds)
            
            if _IS_UNIX:
                ch = os.read(fd, 1)
            else:
                ch = msvcrt.getch()
                
            if ch in (b"\r", b"\n"):
                if cmds:
                    picked = cmds[sel]
                else:
                    picked = buf
                _clear_dropdown_display(buf, cmds)
                print(f"> {picked}")
                return picked
                
            if ch == b"\x03":  # Ctrl+C
                _clear_dropdown_display(buf, cmds)
                return None
                
            if ch == b"\x1b" and _IS_UNIX:  # Escape or Arrow key on Unix
                import select
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    seq = os.read(fd, 2)
                    if seq == b"[A":  # Up Arrow
                        sel = (sel - 1) % len(cmds) if cmds else 0
                    elif seq == b"[B":  # Down Arrow
                        sel = (sel + 1) % len(cmds) if cmds else 0
                else:
                    _clear_dropdown_display(buf, cmds)
                    return None
                continue
                
            if ch == b"\xe0" and not _IS_UNIX:  # Special key on Windows (arrows)
                next_ch = msvcrt.getch()
                if next_ch == b"H":  # Up Arrow
                    sel = (sel - 1) % len(cmds) if cmds else 0
                elif next_ch == b"P":  # Down Arrow
                    sel = (sel + 1) % len(cmds) if cmds else 0
                continue
                
            if ch == b"\x00" and not _IS_UNIX:  # Alternative special key on Windows
                msvcrt.getch()
                continue

            if ch in (b"\x7f", b"\x08"):  # Backspace
                buf = buf[:-1] if len(buf) > 1 else ""
                sel = 0
                if not buf:
                    _clear_dropdown_display(buf, cmds)
                    return None
                continue
                
            if ch == b"\x1b" and not _IS_UNIX:  # Escape on Windows
                _clear_dropdown_display(buf, cmds)
                return None
                
            try:
                c = ch.decode("utf-8", errors="ignore")
                if c.isprintable():
                    buf += c
                    sel = 0
            except Exception:
                pass
    finally:
        if _IS_UNIX and old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _draw_cmd_dropdown(buf: str, cmds: list[str], sel: int, all_cmds: dict[str, str]) -> None:
    """Render the command dropdown in-place below the prompt."""
    sys.stdout.write("\x1b[?25l")
    sys.stdout.write("\r\x1b[J")
    sys.stdout.write(f"> {buf}")
    
    N = len(cmds)
    if N > 0:
        max_cmd_len = max(len(c) for c in cmds)
        for i, cmd in enumerate(cmds):
            sys.stdout.write("\n")
            desc = all_cmds.get(cmd, "")
            padded_cmd = cmd.ljust(max_cmd_len + 4)
            
            if i == sel:
                sys.stdout.write(f"\033[48;5;238m\033[38;5;255m {padded_cmd}{desc} \033[0m")
            else:
                sys.stdout.write(f"\033[38;5;255m{padded_cmd}\033[38;5;244m{desc}\033[0m")
        sys.stdout.write(f"\x1b[{N}A")
        
    sys.stdout.write(f"\r\x1b[{len('> ') + len(buf)}C")
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def _pick_model_interactive(models: list[dict], current_model: str) -> str | None:
    """Arrow-key interactive model picker with viewport scrolling.
    Returns selected model id or None."""
    ids = [m.get("id", m.get("name", "?")) for m in models]
    if not ids:
        return None

    sel = 0
    for i, mid in enumerate(ids):
        if mid == current_model:
            sel = i
            break

    if _IS_UNIX:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
    else:
        fd = None
        old = None

    options = list(ids) + ["Cancel"]
    try:
        while True:
            _render_boxed_picker("Model Picker", f"Select a model ({len(ids)} available)", options, sel, current_model)
            
            if _IS_UNIX:
                ch = os.read(fd, 1)
            else:
                ch = msvcrt.getch()
                
            if ch in (b"\r", b"\n"):
                if sel == len(options) - 1: # Cancel
                    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                    sys.stdout.flush()
                    return None
                sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                sys.stdout.flush()
                return ids[sel]
            if ch == b"\x03" or ch == b"q" or (ch == b"\x1b" and not _IS_UNIX):
                sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                sys.stdout.flush()
                return None
            if ch == b"\x1b" and _IS_UNIX:
                import select
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    seq = os.read(fd, 2)
                    if seq == b"[A":
                        sel = (sel - 1) % len(options)
                    elif seq == b"[B":
                        sel = (sel + 1) % len(options)
            elif ch == b"\xe0" and not _IS_UNIX:
                next_ch = msvcrt.getch()
                if next_ch == b"H":
                    sel = (sel - 1) % len(options)
                elif next_ch == b"P":
                    sel = (sel + 1) % len(options)
    finally:
        if _IS_UNIX and old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def sync_gcs_at_startup(cfg: Config) -> None:
    if not cfg.gcs_bucket:
        return
    try:
        from sudo.core.tools import _get_gcs_client
        client = _get_gcs_client()
        
        from sudo.core.skills import SKILLS_FILE
        if SKILLS_FILE.exists():
            cloud_skills_text = client.read_file_text("skills.json")
            if cloud_skills_text:
                import json
                try:
                    cloud_skills = json.loads(cloud_skills_text)
                    local_skills = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
                    merged = {**cloud_skills, **local_skills}
                    SKILLS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
                    blob = client._bucket.blob("skills.json")
                    client._retry(blob.upload_from_string, json.dumps(merged, indent=2))
                except Exception:
                    pass
            else:
                blob = client._bucket.blob("skills.json")
                client._retry(blob.upload_from_string, SKILLS_FILE.read_text(encoding="utf-8"))
        else:
            cloud_skills_text = client.read_file_text("skills.json")
            if cloud_skills_text:
                SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
                SKILLS_FILE.write_text(cloud_skills_text, encoding="utf-8")
                
        from sudo.core.memory import MEMORY_FILE
        if MEMORY_FILE.exists():
            cloud_mem_text = client.read_file_text("memory.json")
            if cloud_mem_text:
                import json
                try:
                    cloud_mem = json.loads(cloud_mem_text)
                    local_mem = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                    merged = list(set(cloud_mem + local_mem))
                    MEMORY_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
                    blob = client._bucket.blob("memory.json")
                    client._retry(blob.upload_from_string, json.dumps(merged, indent=2))
                except Exception:
                    pass
            else:
                blob = client._bucket.blob("memory.json")
                client._retry(blob.upload_from_string, MEMORY_FILE.read_text(encoding="utf-8"))
        else:
            cloud_mem_text = client.read_file_text("memory.json")
            if cloud_mem_text:
                MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
                MEMORY_FILE.write_text(cloud_mem_text, encoding="utf-8")
    except Exception:
        pass


def rebuild_system_instructions(messages: list[dict], cfg: Config, active_skill_prompt: Optional[str] = None) -> None:
    prompt = SYSTEM_PROMPT
    prompt += "\n\nPersistent Memory Instructions:\nWhenever you learn a new user preference, style detail, technical rule, or key lesson from this conversation, output it wrapped in `<memory>...</memory>` tags (e.g. `<memory>User prefers snake_case for test names</memory>`). These will be saved automatically to improve your work over time."
    if cfg.personality:
        prompt += f"\n\nPersonality / Custom Instructions:\n{cfg.personality}"
    from sudo.core.memory import load_memories
    memories = load_memories()
    if memories:
        prompt += "\n\nStored Memories:\n" + "\n".join(f"- {m}" for m in memories)
    from sudo.core.skills import get_skill
    always_on_prompts = []
    for skill_name in cfg.always_on_skills:
        sk = get_skill(skill_name)
        if sk:
            always_on_prompts.append(f"[{skill_name}]: {sk['system_prompt']}")
    if always_on_prompts:
        prompt += "\n\nActive Always-On Skills:\n" + "\n\n".join(always_on_prompts)
    if active_skill_prompt:
        prompt += f"\n\nActive Skill Prompt:\n{active_skill_prompt}"
    if messages:
        if messages[0].get("role") == "system":
            messages[0]["content"] = prompt
        else:
            messages.insert(0, {"role": "system", "content": prompt})
    else:
        messages.append({"role": "system", "content": prompt})


# ── Main Session Execution Loop ──────────────────────────────────────────────

def run_chat(args) -> int:
    quiet = getattr(args, "quiet", False)
    pipe_input = getattr(args, "pipe_input", None)
    json_output = getattr(args, "json_output", False)
    continue_session = getattr(args, "continue_session", None)

    if not quiet:
        print_banner(__version__)
    if not check_and_run_setup():
        if not quiet:
            print("\033[31mError: Chat session cannot start without configuration.\033[0m")
        return 1

    cfg = load()
    if cfg.gcs_bucket:
        if not quiet:
            print("\033[36mSyncing skills and memories with GCS...\033[0m")
        sync_gcs_at_startup(cfg)
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
    from sudo.core.mcp import initialize_mcp_servers, shutdown_mcp_servers
    from sudo.core.telegram import start_telegram_listener, stop_telegram_listener
    initialize_mcp_servers()
    start_telegram_listener(cfg)

    sm = SessionManager()
    session_data = sm.load()
    run_hooks("on_chat_start", cfg, provider)
    
    # Load or continue session
    target_session_id = None
    if continue_session is not None:
        s_dir = Path.home() / ".config" / "sudo" / "sessions"
        if s_dir.exists():
            files = sorted(s_dir.glob("session_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            if continue_session == "":
                if files:
                    target_session_id = files[0].stem
            else:
                for f in files:
                    if f.stem == continue_session or f.stem == f"session_{continue_session}":
                        target_session_id = f.stem
                        break
                if not target_session_id:
                    for f in files:
                        title = session_data.get(f.stem + "_title", "")
                        if title.strip().lower() == continue_session.strip().lower():
                            target_session_id = f.stem
                            break
    
    if target_session_id:
        active_session_id = target_session_id
        session_data["active_session_id"] = active_session_id
        sm.save(session_data)
        messages = load_active_session_messages(active_session_id)
        rebuild_system_instructions(messages, cfg)
        if not quiet:
            print(f"\033[32mContinuing session: {active_session_id}\033[0m")
            title = session_data.get(active_session_id + "_title")
            if title:
                print(f"  Title: \033[1m{title}\033[0m")
            print()
    else:
        # Start a new session each run
        active_session_id = f"session_{int(time.time())}"
        session_data["active_session_id"] = active_session_id
        sm.save(session_data)
        messages = []
        rebuild_system_instructions(messages, cfg)
        save_active_session_messages(active_session_id, messages)
    
    last_response_time = -1.0
    start_time = time.time()
    
    # Current session tokens tracking
    session_prompt_tokens = 0
    session_completion_tokens = 0
    
    # Session attachments staging
    current_attachments: list[dict[str, str]] = []
    
    # Set up prompt session (command dropdown is handled separately)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory

        _session = PromptSession(history=InMemoryHistory())
    except ImportError:
        _session = None

    # Handle pipe mode — process initial input non-interactively
    if pipe_input and not sys.stdin.isatty():
        user_msg = {"role": "user", "content": pipe_input}
        messages.append(user_msg)

        response_start = time.time()
        current_response = ""
        usage_stats = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            messages = trim_context(messages, provider.model)
            raw_response = []
            def raw_logger(stream):
                for chunk in stream:
                    raw_response.append(chunk)
                    yield chunk

            logged_stream = raw_logger(chat_stream(provider, messages, usage_stats=usage_stats))
            filtered_stream = stream_filter_think_tags(logged_stream)

            visible_response = ""
            for chunk in filtered_stream:
                if not quiet:
                    print(chunk, end="", flush=True)
                visible_response += chunk

            current_response = "".join(raw_response)
            if not visible_response.strip() and current_response.strip() and not quiet:
                print(current_response, end="", flush=True)
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

    # Print initial status bar after banner
    if not quiet:
        print_status_bar(provider.model, messages, last_response_time, start_time)

    while True:
        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            
            from sudo.core.telegram import TELEGRAM_QUEUE
            user_input = None
            first = None
            
            while True:
                if not TELEGRAM_QUEUE.empty():
                    msg_text = TELEGRAM_QUEUE.get()
                    sys.stdout.write(f"{msg_text}\n")
                    sys.stdout.flush()
                    user_input = msg_text
                    break
                
                if _IS_UNIX:
                    import select
                    r, w, x = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        fd = sys.stdin.fileno()
                        first = os.read(fd, 1)
                        break
                else:
                    if msvcrt.kbhit():
                        first = msvcrt.getch()
                        break
                
                time.sleep(0.05)

            if first is not None:
                if first in (b"\xe0", b"\x00") and not _IS_UNIX:
                    msvcrt.getch()
                    continue
                if first != b"/" and not _IS_UNIX:
                    sys.stdout.write(first.decode("utf-8", errors="ignore"))
                    sys.stdout.flush()

                if first in (b"\x03", b""):
                    print("\nExiting chat. Goodbye!")
                    break
                if first == b"\x1b" and _IS_UNIX:
                    seq = os.read(fd, 2)
                    continue
                if first == b"/":
                    picked = _pick_command_dropdown("/")
                    if not picked:
                        continue
                    user_input = picked
                else:
                    rest = first.decode("utf-8", errors="ignore")
                    try:
                        if _session is not None:
                            rest += _session.prompt("").strip()
                        else:
                            rest += input("").strip()
                    except (KeyboardInterrupt, EOFError):
                        print("\nExiting chat. Goodbye!")
                        break
                    user_input = rest
                
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
                elif cmd in ("/help", "/h"):
                    print("Commands:")
                    print("  /new [name]      Start a new session (fresh session ID + history)")
                    print("  /reset           Start a new session (alias for /new)")
                    print("  /clear           Clear conversation history for current session")
                    print("  /connect [p] [m] Switch provider and/or model")
                    print("  /model [name]    Show or change model")
                    print("  /sessions [cmd]  Manage sessions (list, load, new, delete, export, import)")
                    print("  /usage           Show token usage and cost stats")
                    print("  /paste <path>    Attach an image/video to the next message")
                    print("  /save            Save the current conversation to a file")
                    print("  /retry           Retry the last message (resend to agent)")
                    print("  /undo [N]        Back up N user turns and re-prompt (default 1)")
                    print("  /title [name]    Set a title for the current session")
                    print("  /history         Show conversation history")
                    print("  /redraw          Force a full UI repaint (recovers from terminal drift)")
                    print("  /branch [name]   Branch the current session (explore a different path)")
                    print("  /fork            Branch the current session (alias for /branch)")
                    print("  /help            Show this message")
                    print("  /exit, /quit     Exit chat")
                    print()
                    continue
                elif cmd == "/sessions":
                    active_session_id, messages = handle_sessions_cmd(
                        cmd_arg, session_data, sm, current_messages=messages
                    )
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
                elif cmd in ("/new", "/reset"):
                    active_session_id = f"session_{int(time.time())}"
                    session_data["active_session_id"] = active_session_id
                    sm.save(session_data)
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    save_active_session_messages(active_session_id, messages)
                    last_response_time = -1.0
                    current_attachments.clear()
                    session_prompt_tokens = 0
                    session_completion_tokens = 0
                    if cmd_arg:
                        session_data[active_session_id + "_title"] = cmd_arg
                        sm.save(session_data)
                        print(f"\033[32mNew session: {active_session_id} — \"{cmd_arg}\"\033[0m\n")
                    else:
                        print(f"\033[32mNew session: {active_session_id}\033[0m\n")
                    continue
                elif cmd == "/history":
                    print(f"\n\033[1mSession: {active_session_id}\033[0m")
                    print(f"  Messages: {len(messages)}")
                    for i, m in enumerate(messages):
                        role = m.get("role", "?")
                        preview = m.get("content", "")[:80].replace("\n", " ")
                        print(f"  {i}: [{role}] {preview}{'...' if len(m.get('content', '')) > 80 else ''}")
                    print()
                    continue
                elif cmd == "/save":
                    save_active_session_messages(active_session_id, messages)
                    print(f"\033[32mSession {active_session_id} saved.\033[0m\n")
                    continue
                elif cmd == "/retry":
                    # Remove last assistant message and retry
                    if len(messages) > 1 and messages[-1].get("role") == "assistant":
                        messages.pop()
                        print(f"\033[33mRemoved last assistant response. Retrying...\033[0m")
                        # Find the last user message to re-send
                        retry_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
                        if retry_msg:
                            user_input = retry_msg
                            # Fall through to LLM processing below
                        else:
                            print("\033[31mNo user message to retry.\033[0m\n")
                            continue
                    else:
                        print("\033[31mNo assistant response to retry.\033[0m\n")
                        continue
                elif cmd == "/undo":
                    n = 1
                    if cmd_arg and cmd_arg.isdigit():
                        n = int(cmd_arg)
                    removed = 0
                    for _ in range(n):
                        # Remove assistant response
                        if messages and messages[-1].get("role") == "assistant":
                            messages.pop()
                            removed += 1
                        # Remove preceding user message
                        if messages and messages[-1].get("role") == "user":
                            messages.pop()
                            removed += 1
                    save_active_session_messages(active_session_id, messages)
                    print(f"\033[33mUndid {n} turn(s) ({removed} message(s) removed).\033[0m\n")
                    continue
                elif cmd == "/title":
                    if cmd_arg:
                        session_data[active_session_id + "_title"] = cmd_arg
                        sm.save(session_data)
                        print(f"\033[32mSession titled: \"{cmd_arg}\"\033[0m\n")
                    else:
                        title = session_data.get(active_session_id + "_title", "")
                        print(f"Session title: \033[1m{title or '(none)'}\033[0m")
                        print("Usage: /title <name>")
                    continue
                elif cmd == "/redraw":
                    print("\033[2J\033[H", end="", flush=True)
                    _status_bar_printed = False  # screen cleared, reset tracking
                    if not quiet:
                        print_banner(__version__)
                    print_status_bar(provider.model, messages, last_response_time, start_time)
                    continue
                elif cmd in ("/branch", "/fork"):
                    branch_id = f"{active_session_id}_branch_{int(time.time())}"
                    if cmd_arg:
                        session_data[branch_id + "_title"] = cmd_arg
                    # Copy current messages to branch
                    save_active_session_messages(branch_id, messages)
                    print(f"\033[32mBranched session: {branch_id}\033[0m\n")
                    continue
                elif cmd in ("/model", "/models"):
                    if not cmd_arg:
                        print("Fetching available models from provider...")
                        try:
                            models = provider.list_models()
                            if not models:
                                print("No models returned by provider.\n")
                                continue
                        except Exception as e:
                            print(f"Error fetching models: {e}\n")
                            continue
                        picked = _pick_model_interactive(models, provider.model)
                        if not picked or picked == provider.model:
                            print()
                            continue
                        cfg.model = picked
                        save(cfg)
                        try:
                            pc = cfg.get_provider_config()
                            provider = ProviderFactory.create(pc.name, api_key=pc.api_key, model=picked, base_url=pc.base_url)
                            print(f"\033[32mModel changed to \033[1m{picked}\033[0m\033[0m\n")
                        except Exception as e:
                            print(f"\033[31mError updating provider: {e}\033[0m\n")
                    else:
                        new_model = cmd_arg
                        # Validate model against provider's available models
                        try:
                            fetched = provider.list_models()
                            known_ids = [m.get("id", m.get("name", "")) for m in fetched]
                            if new_model not in known_ids:
                                print(f"\033[31m✗ Model '{new_model}' is not available for this provider.\033[0m\n")
                                continue
                        except Exception:
                            pass

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
                elif cmd in ("/help", "/h"):
                    print("Commands:")
                    print("  /new [name]      Start a new session (fresh session ID + history)")
                    print("  /reset           Start a new session (alias for /new)")
                    print("  /clear           Clear conversation history for current session")
                    print("  /connect [p] [m] Switch provider and/or model")
                    print("  /model [name]    Show or change model")
                    print("  /sessions [cmd]  Manage sessions (list, load, new, delete, export, import)")
                    print("  /resume [name]   Resume/load a previous session")
                    print("  /personality [t] View or set custom system instructions")
                    print("  /btw <question>  Ask a quick side question (doesn't affect chat history)")
                    print("  /config          View or modify configuration values")
                    print("  /reasoning       Toggle displaying thinking/reasoning outputs")
                    print("  /yolo            Toggle YOLO mode (skip dangerous commands prompts)")
                    print("  /tools           List all registered assistant tools")
                    print("  /memory          Manage preferences memory (add, delete, list, clear)")
                    print("  /cron            List active cron/scheduled tasks on the system")
                    print("  /mcp-reload      Reload Model Context Protocol (MCP) server tools")
                    print("  /skills-reload   Reload available skills from configuration")
                    print("  /gcs-config      Configure Google Cloud Storage bucket/keys")
                    print("  /usage           Show token usage and cost stats")
                    print("  /paste <path>    Attach an image/video to the next message")
                    print("  /save            Save the current conversation to a file")
                    print("  /retry           Retry the last message (resend to agent)")
                    print("  /undo [N]        Back up N user turns and re-prompt (default 1)")
                    print("  /title [name]    Set a title for the current session")
                    print("  /history         Show conversation history")
                    print("  /redraw          Force a full UI repaint (recovers from terminal drift)")
                    print("  /branch [name]   Branch the current session (explore a different path)")
                    print("  /fork            Branch the current session (alias for /branch)")
                    print("  /help            Show this message")
                    print("  /exit, /quit     Exit chat")
                    continue
                elif cmd == "/skills":
                    from sudo.core.skills import load_skills, delete_skill, DEFAULT_SKILLS
                    parts = cmd_arg.split(None, 1)
                    sub = parts[0].lower() if parts else ""
                    sub_arg = parts[1].strip() if len(parts) > 1 else ""
                    
                    if sub == "delete":
                        if not sub_arg:
                            print("\033[31mError: Specify skill name to delete.\033[0m\n")
                            continue
                        if delete_skill(sub_arg):
                            print(f"\033[32mSkill '{sub_arg}' successfully deleted.\033[0m\n")
                        else:
                            print(f"\033[31mError: Skill '{sub_arg}' not found or is a built-in skill.\033[0m\n")
                        continue
                    
                    skills = load_skills()
                    print("\n\033[1mAvailable Skills:\033[0m")
                    for name, info in skills.items():
                        is_builtin = " (built-in)" if name in DEFAULT_SKILLS else ""
                        print(f"  \033[1;36m/{name}\033[0m{is_builtin} — {info['description']}")
                    print("\nUsage:")
                    print("  /<skill_name> <prompt>   Run prompt using the skill's system instructions")
                    print("  /skills delete <name>    Delete a custom skill")
                    print()
                    continue
                elif cmd == "/personality":
                    if not cmd_arg:
                        print(f"Current Personality: \033[1m{cfg.personality or '(none)'}\033[0m")
                        print("Usage: /personality <personality instructions>")
                    else:
                        cfg.personality = cmd_arg
                        save(cfg)
                        rebuild_system_instructions(messages, cfg)
                        save_active_session_messages(active_session_id, messages)
                        print("\033[32mPersonality updated.\033[0m")
                    print()
                    continue
                elif cmd == "/btw":
                    if not cmd_arg:
                        print("Usage: /btw <side question>\n")
                        continue
                    print(f"\033[36m[btw] Asking side question: {cmd_arg}...\033[0m")
                    temp_msgs = []
                    rebuild_system_instructions(temp_msgs, cfg)
                    temp_msgs.append({"role": "user", "content": cmd_arg})
                    
                    tw = terminal_width()
                    print(f"\033[38;5;208m─  ⚡ SUDO BTW  " + "─" * (tw - 16) + "\033[0m")
                    try:
                        spinner = SpinnerThread()
                        spinner.start_cycling(get_subjective_states(cmd_arg))
                        spinner.start()

                        usage_stats = {"prompt_tokens": 0, "completion_tokens": 0}
                        stream = chat_stream(provider, temp_msgs, usage_stats=usage_stats)
                        if not cfg.show_reasoning:
                            stream = stream_filter_think_tags(stream)
                            
                        spinner_stopped = False
                        for chunk in stream:
                            if not spinner_stopped:
                                spinner.stop()
                                spinner_stopped = True
                            print(chunk, end="", flush=True)
                        if not spinner_stopped:
                            spinner.stop()
                        print()
                        
                        session_prompt_tokens += usage_stats["prompt_tokens"]
                        session_completion_tokens += usage_stats["completion_tokens"]
                        add_cumulative_usage(usage_stats["prompt_tokens"], usage_stats["completion_tokens"])
                    except Exception as e:
                        if 'spinner' in locals() or 'spinner' in globals():
                            spinner.stop()
                        print(f"\033[31mError during side question: {e}\033[0m")
                    print("\033[38;5;208m" + "─" * tw + "\033[0m\n")
                    continue
                elif cmd == "/resume":
                    target_session_id = None
                    s_dir = Path.home() / ".config" / "sudo" / "sessions"
                    if s_dir.exists():
                        files = sorted(s_dir.glob("session_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
                        if not cmd_arg:
                            print("\n\033[1mAvailable Sessions:\033[0m")
                            for idx, f in enumerate(files[:10], 1):
                                title = session_data.get(f.stem + "_title", "")
                                title_str = f" (\"{title}\")" if title else ""
                                active_str = " \033[32m[active]\033[0m" if f.stem == active_session_id else ""
                                print(f"  {idx:2d}. {f.stem}{title_str}{active_str}")
                            print("\nUsage: /resume <session_id_or_title>\n")
                            continue
                        else:
                            for f in files:
                                if f.stem == cmd_arg or f.stem == f"session_{cmd_arg}":
                                    target_session_id = f.stem
                                    break
                            if not target_session_id:
                                for f in files:
                                    title = session_data.get(f.stem + "_title", "")
                                    if title.strip().lower() == cmd_arg.strip().lower():
                                        target_session_id = f.stem
                                        break
                    if target_session_id:
                        active_session_id = target_session_id
                        session_data["active_session_id"] = active_session_id
                        sm.save(session_data)
                        messages = load_active_session_messages(active_session_id)
                        rebuild_system_instructions(messages, cfg)
                        save_active_session_messages(active_session_id, messages)
                        print(f"\033[32mResumed session: {active_session_id}\033[0m\n")
                    else:
                        print(f"\033[31mError: Session '{cmd_arg}' not found.\033[0m\n")
                    continue
                elif cmd == "/config":
                    parts = cmd_arg.split(None, 1)
                    sub = parts[0].lower() if parts else ""
                    sub_val = parts[1].strip() if len(parts) > 1 else ""
                    
                    if sub == "yolo":
                        cfg.yolo_mode = sub_val.lower() in ("on", "true", "yes", "1")
                        save(cfg)
                    elif sub == "reasoning":
                        cfg.show_reasoning = sub_val.lower() in ("on", "true", "yes", "1")
                        save(cfg)
                    elif sub == "bucket":
                        cfg.gcs_bucket = sub_val
                        save(cfg)
                    elif sub == "keyfile":
                        cfg.gcs_key_file = sub_val
                        save(cfg)
                    elif sub == "personality":
                        cfg.personality = sub_val
                        save(cfg)
                        rebuild_system_instructions(messages, cfg)
                        save_active_session_messages(active_session_id, messages)
                    elif sub == "tg_token":
                        cfg.telegram_token = sub_val
                        save(cfg)
                    elif sub == "tg_chat_id":
                        cfg.telegram_chat_id = sub_val
                        save(cfg)
                    elif sub == "tg_enabled":
                        cfg.telegram_enabled = sub_val.lower() in ("on", "true", "yes", "1")
                        save(cfg)
                        
                    print("\n\033[1msudo Configuration:\033[0m")
                    print(f"  \033[1mProvider:\033[0m         {cfg.provider or '(none)'}")
                    print(f"  \033[1mModel:\033[0m            {cfg.model or '(none)'}")
                    print(f"  \033[1mYOLO Mode:\033[0m        {cfg.yolo_mode}")
                    print(f"  \033[1mShow Reasoning:\033[0m   {cfg.show_reasoning}")
                    print(f"  \033[1mGCS Bucket:\033[0m       {cfg.gcs_bucket or '(none)'}")
                    print(f"  \033[1mGCS Key File:\033[0m     {cfg.gcs_key_file or '(none)'}")
                    print(f"  \033[1mTelegram Enabled:\033[0m {cfg.telegram_enabled}")
                    print(f"  \033[1mTelegram ChatID:\033[0m  {cfg.telegram_chat_id or '(none)'}")
                    print(f"  \033[1mTelegram Token:\033[0m   {cfg.telegram_token or '(none)'}")
                    print(f"  \033[1mAlways-on:\033[0m       {', '.join(cfg.always_on_skills) or '(none)'}")
                    print(f"  \033[1mPersonality:\033[0m     {cfg.personality or '(none)'}")
                    print("\nUsage:")
                    print("  /config yolo [on|off]")
                    print("  /config reasoning [on|off]")
                    print("  /config bucket <name>")
                    print("  /config keyfile <path>")
                    print("  /config personality <text>")
                    print("  /config tg_enabled [on|off]")
                    print("  /config tg_token <bot_token>")
                    print("  /config tg_chat_id <chat_id>")
                    print()
                    continue
                elif cmd == "/reasoning":
                    cfg.show_reasoning = not cfg.show_reasoning
                    save(cfg)
                    print(f"\033[32mShow reasoning outputs: {cfg.show_reasoning}\033[0m\n")
                    continue
                elif cmd == "/yolo":
                    cfg.yolo_mode = not cfg.yolo_mode
                    save(cfg)
                    print(f"\033[32mYOLO Mode: {cfg.yolo_mode} (dangerous command prompt warnings are {'DISABLED' if cfg.yolo_mode else 'ENABLED'})\033[0m\n")
                    continue
                elif cmd == "/tools":
                    from sudo.core.tools import TOOL_REGISTRY
                    print("\n\033[1mRegistered Tools:\033[0m")
                    for name, spec in sorted(TOOL_REGISTRY.items()):
                        status = " [disabled]" if spec.disabled else ""
                        print(f"  \033[1;36m{name}\033[0m{status} — {spec.description}")
                    print()
                    continue
                elif cmd == "/memory":
                    from sudo.core.memory import load_memories, add_memory, delete_memory, clear_memories
                    parts = cmd_arg.split(None, 1)
                    sub = parts[0].lower() if parts else ""
                    sub_val = parts[1].strip() if len(parts) > 1 else ""
                    
                    if sub == "add":
                        if not sub_val:
                            print("\033[31mError: Memory content cannot be empty.\033[0m\n")
                            continue
                        add_memory(sub_val)
                        rebuild_system_instructions(messages, cfg)
                        save_active_session_messages(active_session_id, messages)
                        print("\033[32mPreference added to memory.\033[0m\n")
                        continue
                    elif sub in ("delete", "remove"):
                        if not sub_val.isdigit():
                            print("\033[31mError: Specify memory index (number) to delete.\033[0m\n")
                            continue
                        if delete_memory(int(sub_val)):
                            rebuild_system_instructions(messages, cfg)
                            save_active_session_messages(active_session_id, messages)
                            print("\033[32mMemory deleted successfully.\033[0m\n")
                        else:
                            print("\033[31mError: Invalid memory index.\033[0m\n")
                        continue
                    elif sub == "clear":
                        clear_memories()
                        rebuild_system_instructions(messages, cfg)
                        save_active_session_messages(active_session_id, messages)
                        print("\033[32mAll memories cleared.\033[0m\n")
                        continue
                    
                    memories = load_memories()
                    print("\n\033[1mStored Preferences & Memories:\033[0m")
                    if not memories:
                        print("  (none)")
                    else:
                        for idx, mem in enumerate(memories, 1):
                            print(f"  {idx:2d}. {mem}")
                    print("\nUsage:")
                    print("  /memory add <preference text>")
                    print("  /memory delete <index>")
                    print("  /memory clear")
                    print()
                    continue
                elif cmd == "/cron":
                    import subprocess
                    import os
                    print("\n\033[1mSystem Cron / Scheduled Tasks:\033[0m")
                    if os.name == 'nt':
                        try:
                            res = subprocess.run("schtasks /query /fo TABLE", shell=True, capture_output=True, text=True, timeout=10)
                            lines = (res.stdout or res.stderr).splitlines()
                            for line in lines[:25]:
                                print(line)
                            if len(lines) > 25:
                                print(f"  ... [truncated {len(lines)-25} lines]")
                        except Exception as e:
                            print(f"Failed to query schtasks: {e}")
                    else:
                        try:
                            res = subprocess.run("crontab -l", shell=True, capture_output=True, text=True, timeout=10)
                            print(res.stdout or res.stderr)
                        except Exception as e:
                            print(f"Failed to query crontab: {e}")
                    print()
                    continue
                elif cmd == "/mcp-reload":
                    from sudo.core.mcp import initialize_mcp_servers
                    initialize_mcp_servers()
                    print("\033[32mMCP servers reloaded.\033[0m\n")
                    continue
                elif cmd == "/skills-reload":
                    from sudo.core.skills import load_skills
                    load_skills()
                    print("\033[32mSkills reloaded.\033[0m\n")
                    continue
                elif cmd == "/gcs-config":
                    parts = cmd_arg.split(None, 1)
                    sub = parts[0].lower() if parts else ""
                    sub_val = parts[1].strip() if len(parts) > 1 else ""
                    
                    if sub == "bucket":
                        cfg.gcs_bucket = sub_val
                        save(cfg)
                        print(f"\033[32mGCS bucket updated to: {sub_val}\033[0m\n")
                        continue
                    elif sub == "keyfile":
                        cfg.gcs_key_file = sub_val
                        save(cfg)
                        print(f"\033[32mGCS key file updated to: {sub_val}\033[0m\n")
                        continue
                    
                    print("\n\033[1mGoogle Cloud Storage (GCS) Config:\033[0m")
                    print(f"  \033[1mGCS Bucket:\033[0m       {cfg.gcs_bucket or '(none)'}")
                    print(f"  \033[1mGCS Key File:\033[0m     {cfg.gcs_key_file or '(none)'}")
                    print("\nUsage:")
                    print("  /gcs-config bucket <bucket_name>")
                    print("  /gcs-config keyfile <path_to_service_account_key.json>")
                    print()
                    continue
                else:
                    from sudo.core.skills import load_skills
                    skills = load_skills()
                    skill_name = cmd[1:]
                    if skill_name in skills:
                        always_add = False
                        always_remove = False
                        if cmd_arg == "+=always" or cmd_arg.endswith("+=always"):
                            always_add = True
                            cmd_arg = cmd_arg.replace("+=always", "").strip()
                        elif cmd_arg == "-=always" or cmd_arg.endswith("-=always"):
                            always_remove = True
                            cmd_arg = cmd_arg.replace("-=always", "").strip()
                            
                        if always_add:
                            if skill_name not in cfg.always_on_skills:
                                cfg.always_on_skills.append(skill_name)
                                save(cfg)
                            print(f"\033[32mSkill '{skill_name}' is now always active.\033[0m\n")
                            rebuild_system_instructions(messages, cfg)
                            save_active_session_messages(active_session_id, messages)
                            continue
                        elif always_remove:
                            if skill_name in cfg.always_on_skills:
                                cfg.always_on_skills.remove(skill_name)
                                save(cfg)
                            print(f"\033[32mSkill '{skill_name}' always-active mode disabled.\033[0m\n")
                            rebuild_system_instructions(messages, cfg)
                            save_active_session_messages(active_session_id, messages)
                            continue
                            
                        if not cmd_arg:
                            print(f"Usage: {cmd} <your prompt>\n")
                            continue
                        
                        skill = skills[skill_name]
                        print(f"\033[36mRunning skill \033[1m{skill_name}\033[0m: {skill['description']}...\033[0m")
                        rebuild_system_instructions(messages, cfg, active_skill_prompt=skill["system_prompt"])
                        user_input = cmd_arg
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
            clear_previous_status_bar(user_input)
            
            response_start = time.time()
            max_turns = 10
            turn = 0
            pushed_to_github = False
            
            while turn < max_turns:
                turn += 1
                tw = terminal_width()
                print(f"\033[38;5;208m─  ⚡ SUDO  " + "─" * (tw - 12) + "\033[0m")
                
                current_response = ""
                usage_stats = {"prompt_tokens": 0, "completion_tokens": 0}
                # Trim context if approaching token limit
                messages = trim_context(messages, provider.model)
                try:
                    spinner = SpinnerThread()
                    spinner.start_cycling(get_subjective_states(user_input))
                    spinner.start()

                    raw_response = []
                    def raw_logger(stream):
                        for chunk in stream:
                            raw_response.append(chunk)
                            yield chunk

                    logged_stream = raw_logger(chat_stream(provider, messages, usage_stats=usage_stats))
                    if cfg.show_reasoning:
                        filtered_stream = logged_stream
                    else:
                        filtered_stream = stream_filter_think_tags(logged_stream)

                    visible_response = ""
                    spinner_stopped = False
                    for chunk in filtered_stream:
                        if not spinner_stopped:
                            spinner.stop()
                            spinner_stopped = True
                        print(chunk, end="", flush=True)
                        visible_response += chunk

                    if not spinner_stopped:
                        spinner.stop()

                    current_response = "".join(raw_response)
                    if not visible_response.strip() and current_response.strip():
                        print(current_response, end="", flush=True)
                except Exception as e:
                    spinner.stop()
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
                        if c.get("name") == "github_push":
                            pushed_to_github = True
                        run_hooks("on_tool_before", c.get("name"), c.get("arguments", {}))

                tool_spinner = None
                if calls:
                    c = calls[0]
                    t_name = c.get("name", "")
                    if t_name in ("read_file", "gcs_read_file"):
                        lbl = "[ ⠹ ] 📄 Reading File..."
                    elif t_name in ("write_file", "gcs_write_file"):
                        lbl = "[ ⠸ ] 📝 Patching File..."
                    elif t_name in ("run_command", "github_push"):
                        lbl = "[ ⠧ ] 💻 Terminal..."
                    elif t_name == "browse":
                        lbl = "[ ⠋ ] 🌐 Browsing..."
                    else:
                        lbl = f"[ ⠦ ] 🛠️  Running Tool {t_name}..."
                    
                    if "gcs" in t_name:
                        lbl = f"(=^・ω・^=) kneading... {lbl}"
                        
                    tool_spinner = SpinnerThread(lbl)
                    tool_spinner.start()

                had_tool_call, tool_output = tools.parse_and_execute_tools(current_response)

                if tool_spinner:
                    tool_spinner.stop()

                if calls:
                    for c in calls:
                        run_hooks("on_tool_after", c.get("name"), c.get("arguments", {}), tool_output)

                if had_tool_call:
                    messages.append({"role": "assistant", "content": current_response})
                    is_error = False
                    if "error" in tool_output.lower() or "exception" in tool_output.lower():
                        is_error = True
                    else:
                        exit_code_match = re.search(r'exit code:\s*([\-0-9]+)', tool_output, re.IGNORECASE)
                        if exit_code_match and exit_code_match.group(1) != "0":
                            is_error = True
                            
                    if is_error:
                        print("\033[31m[ ❌ ] ⚠️  Failed / Halted\033[0m")
                        print(f"\033[31m❌ {tool_output.strip()}\033[0m")
                    else:
                        print("\033[32m[ ✅ ] 🎉 Task Finished!\033[0m")
                        clean_status = tool_output.splitlines()[0] if tool_output.strip() else ""
                        truncated = clean_status[:63] + "..." if len(clean_status) > 63 else clean_status
                        print(f"\033[36m⚙️ {truncated}\033[0m")
                    messages.append({"role": "user", "content": tool_output})
                    save_active_session_messages(active_session_id, messages)
                    continue
                else:
                    if current_response.strip():
                        memories_found = re.findall(r'<memory>(.*?)</memory>', current_response, re.DOTALL)
                        if memories_found:
                            from sudo.core.memory import add_memory
                            for mem in memories_found:
                                add_memory(mem.strip())
                            current_response = re.sub(r'<memory>.*?</memory>', '', current_response, flags=re.DOTALL).strip()
                            
                        messages.append({"role": "assistant", "content": current_response})
                        save_active_session_messages(active_session_id, messages)
                        print("\n\033[32m✧٩(ˊᗜˋ*)و✧ got it!\033[0m")
                        from sudo.core.telegram import send_telegram_message
                        if pushed_to_github:
                            send_telegram_message(cfg, "The task has been completed. Changes pushed.")
                        else:
                            send_telegram_message(cfg, "The task has been completed.")
                    break
                    
            print()
            last_response_time = time.time() - response_start
            if not quiet:
                print_status_bar(provider.model, messages, last_response_time, start_time)
            
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
            
    shutdown_mcp_servers()
    stop_telegram_listener()
    return 0
