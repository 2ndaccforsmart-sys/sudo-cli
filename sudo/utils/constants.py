"""Shared constants for sudo CLI."""

from __future__ import annotations


SOURCE_EXTS: set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash",
    ".zsh", ".fish", ".sql", ".r", ".m", ".cs", ".hs", ".ex", ".exs",
    ".html", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".yaml", ".yml", ".toml", ".json", ".xml", ".md", ".rst",
    ".zig", ".nim", ".cr", ".lua", ".clj", ".cljs", ".erl", ".hrl",
    ".fs", ".fsx", ".dart", ".asm", ".s", ".tex", ".bib",
}


IGNORED_DIRS: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".env", "dist", "build", ".tox", ".eggs", "target",
}


def load_gitignore_patterns(cwd: str) -> set[str]:
    """Load .gitignore patterns from the given directory, merged with defaults."""
    ignored = set(IGNORED_DIRS)
    try:
        import os
        gi = os.path.join(cwd, ".gitignore")
        if os.path.isfile(gi):
            with open(gi) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("!"):
                        ignored.add(line.rstrip("/"))
    except Exception:
        pass
    return ignored


MODEL_COST_RATES: dict[str, tuple[float, float]] = {
    # (input_per_mtok, output_per_mtok) in USD
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "o1-preview": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "deepseek-chat": (0.14, 0.28),
    "deepseek-coder": (0.14, 0.28),
    "gemini-2.0-flash": (0.075, 0.30),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "mistral-large-latest": (2.00, 6.00),
    "sonar-pro": (3.00, 15.00),
}

DEFAULT_COST_RATE = (0.15, 0.60)


def estimate_model_cost(model_name: str) -> tuple[float, float]:
    """Return (input_per_mtok, output_per_mtok) for a model name."""
    model_lower = model_name.lower()
    for pattern, rates in MODEL_COST_RATES.items():
        if pattern in model_lower:
            return rates
    return DEFAULT_COST_RATE
