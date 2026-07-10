"""Token counting utilities for context window management.

Supports tiktoken for OpenAI models, falls back to character-based estimation.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

# Model context window sizes (in tokens)
CONTEXT_WINDOWS: Dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-4-32k": 32_768,
    "gpt-3.5-turbo": 16_384,
    "gpt-3.5-turbo-16k": 16_384,
    "o1-preview": 128_000,
    "o1-mini": 128_000,
    
    # Anthropic
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-2.1": 200_000,
    "claude-2": 100_000,
    
    # Google
    "gemini-1.5-pro": 2_000_000,
    "gemini-1.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.0-flash-exp": 1_000_000,
    "gemini-1.0-pro": 32_768,
    
    # Meta/Llama
    "llama-3.3-70b-versatile": 128_000,
    "llama-3.1-70b-versatile": 128_000,
    "llama-3.1-8b-instant": 128_000,
    "llama-3.2-90b-vision": 128_000,
    "llama-3.2-11b-vision": 128_000,
    "llama3.2": 128_000,
    "llama3.1": 128_000,
    
    # Mistral
    "mistral-large": 128_000,
    "mixtral-8x7b": 32_768,
    "mixtral-8x22b": 64_000,
    
    # Cohere
    "command-r-plus": 128_000,
    "command-r": 128_000,
    
    # Other common models
    "deepseek-chat": 64_000,
    "deepseek-coder": 64_000,
    "qwen-plus": 32_768,
    "qwen-max": 32_768,
}


class TokenCounter:
    """Token counter with tiktoken support and fallback estimation."""
    
    def __init__(self, model: str = ""):
        self.model = model.lower()
        self._encoding = None
        self._init_encoding()
    
    def _init_encoding(self) -> None:
        """Initialize tiktoken encoding for the model."""
        try:
            import tiktoken
            # Try to get encoding for specific model
            if "gpt-4o" in self.model or "gpt-4-turbo" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-4o")
            elif "gpt-4" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-4")
            elif "gpt-3.5" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
            elif "o1" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-4o")
            else:
                # Default to cl100k_base (used by GPT-4/3.5)
                self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._encoding = None
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self._encoding:
            return len(self._encoding.encode(text))
        # Fallback: rough estimation (chars / 4)
        return len(text) // 4
    
    def count_message_tokens(self, message: Dict) -> int:
        """Count tokens in a message dict (role + content)."""
        content = message.get("content", "")
        role = message.get("role", "")
        # Add ~4 tokens for role overhead
        return self.count_tokens(content) + 4
    
    def count_messages_tokens(self, messages: list[Dict]) -> int:
        """Count total tokens in message list."""
        return sum(self.count_message_tokens(m) for m in messages)
    
    def get_context_limit(self) -> int:
        """Get context window limit for the model."""
        # Try exact match first
        if self.model in CONTEXT_WINDOWS:
            return CONTEXT_WINDOWS[self.model]
        
        # Try partial matches
        for key, limit in CONTEXT_WINDOWS.items():
            if key in self.model or self.model in key:
                return limit
        
        # Default fallback
        return 32_000
    
    def get_usage_ratio(self, messages: list[Dict]) -> float:
        """Get current usage ratio (0.0 to 1.0)."""
        used = self.count_messages_tokens(messages)
        limit = self.get_context_limit()
        return used / limit if limit > 0 else 0.0
    
    def should_summarize(self, messages: list[Dict], threshold: float = 0.75) -> bool:
        """Check if context should be summarized."""
        return self.get_usage_ratio(messages) >= threshold


def count_tokens(text: str, model: str = "") -> int:
    """Convenience function to count tokens."""
    return TokenCounter(model).count_tokens(text)


def get_context_limit(model: str) -> int:
    """Get context window limit for a model."""
    return TokenCounter(model).get_context_limit()


def should_summarize(messages: list[Dict], model: str, threshold: float = 0.75) -> bool:
    """Check if conversation should be summarized."""
    return TokenCounter(model).should_summarize(messages, threshold)