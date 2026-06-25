"""'sudo provider' command — manage LLM providers."""

from __future__ import annotations

import os
import sys

from sudo.core.config import load, save
from sudo.core.provider import PROVIDER_REGISTRY, ProviderFactory, TIER_LABELS, TIER_ORDER
from sudo.utils.output import page


def register(subparsers) -> None:
    p = subparsers.add_parser("provider", help="Manage LLM providers")
    p.set_defaults(func=lambda args: list_providers(args))
    subs = p.add_subparsers(title="subcommands", dest="provider_cmd")

    ls = subs.add_parser("list", help="List all available providers")
    ls.set_defaults(func=lambda args: list_providers(args))

    s = subs.add_parser("set", help="Set active provider")
    s.add_argument("name", help="Provider name")
    s.set_defaults(func=lambda args: set_provider(args))

    k = subs.add_parser("key", help="Set API key in config")
    k.add_argument("key", help="API key value")
    k.set_defaults(func=lambda args: set_key(args))

    t = subs.add_parser("test", help="Test current provider by listing models")
    t.set_defaults(func=lambda args: test_provider(args))

    d = subs.add_parser("docs", help="Show documentation URL for a provider")
    d.add_argument("name", nargs="?", default=None, help="Provider name (default: current)")
    d.set_defaults(func=lambda args: show_docs(args))


def _render_table(headers, rows, max_width=66):
    """Render a table as a list of text lines."""
    out = []
    ncols = len(headers)
    col_widths = []
    for i, h in enumerate(headers):
        max_cell = max(len(str(r[i])) if i < len(r) else 0 for r in rows) if rows else 0
        col_widths.append(max(len(h), max_cell))
    total = sum(col_widths) + 3 * (ncols - 1) + 2
    if total > max_width and ncols > 0:
        overflow = total - max_width
        for i in range(ncols):
            if overflow <= 0:
                break
            shrink = min(col_widths[i] - 5, overflow // (ncols - i) if ncols - i > 0 else 0)
            if shrink > 0:
                col_widths[i] -= shrink
                overflow -= shrink

    def _render(cells):
        parts = []
        for i, c in enumerate(cells):
            w = col_widths[i] if i < len(col_widths) else 10
            parts.append(str(c)[:w].ljust(w))
        return " " + "  ".join(parts)

    out.append(_render(headers))
    out.append("-" * max_width)
    for row in rows:
        out.append(_render(row[:ncols]))
    return out


def list_providers(args) -> None:
    lines = [f"sudo Providers — {len(PROVIDER_REGISTRY)} total", ""]
    for tier in TIER_ORDER:
        provs = [(n, d) for n, d in PROVIDER_REGISTRY.items() if d.tier == tier]
        if not provs:
            continue
        lines.append(f"  {TIER_LABELS.get(tier, f'Tier {tier}')}")
        lines.append("")
        provs.sort(key=lambda x: (not x[1].free_tier, x[0]))
        headers = ["Name", "Default Model", "Free", "Key Set?"]
        rows = []
        for name, defn in provs:
            key_set = "✓" if os.environ.get(defn.env_key) else ""
            free_tag = "✓" if defn.free_tier else ""
            rows.append([name, defn.default_model, free_tag, key_set])
        for line in _render_table(headers, rows):
            lines.append(f"    {line}")
        lines.append("")
    page("\n".join(lines))


def set_provider(args) -> None:
    name = args.name
    if name not in PROVIDER_REGISTRY:
        print(f"Unknown provider '{name}'. Use 'sudo provider list' to see options.")
        sys.exit(1)
    cfg = load()
    cfg.provider = name
    save(cfg)
    defn = PROVIDER_REGISTRY[name]
    key_env = os.environ.get(defn.env_key)
    status = f"✓ API key found in environment" if key_env else f"✗ No API key — set {defn.env_key} or use 'sudo provider key <key>'"
    print(f"Active provider: {defn.display} ({name})")
    print(f"  Default model: {defn.default_model}")
    print(f"  {status}")
    print(f"  Get a key at: {defn.docs_url}")


def set_key(args) -> None:
    cfg = load()
    cfg.api_key = args.key
    save(cfg)
    print("API key saved to config.")


def test_provider(args) -> None:
    cfg = load()
    if not cfg.provider:
        print("No provider configured. Use 'sudo provider set <name>' first.")
        sys.exit(1)
    pc = cfg.get_provider_config()
    print(f"Testing provider: {cfg.provider}")
    print(f"  Model: {pc.model or '(default)'}")
    print("  Listing models... ", end="", flush=True)
    try:
        provider = ProviderFactory.create(pc.name, api_key=pc.api_key, model=pc.model, base_url=pc.base_url)
        models = provider.list_models()
        print("OK!")
        print(f"  Found {len(models)} model(s)")
        if models:
            for m in models[:5]:
                mid = m.get("id", m.get("name", "?"))
                print(f"    \u2022 {mid}")
            if len(models) > 5:
                print(f"    ... and {len(models) - 5} more")
    except Exception as e:
        print("FAILED")
        print(f"  Error: {e}")
        sys.exit(1)


def show_docs(args) -> None:
    name = args.name
    if not name:
        cfg = load()
        name = cfg.provider
        if not name:
            print("No provider set. Specify a name: sudo provider docs <name>")
            sys.exit(1)
    defn = PROVIDER_REGISTRY.get(name)
    if not defn:
        print(f"Unknown provider '{name}'.")
        sys.exit(1)
    print(f"[{defn.display}]")
    print(f"  Website: {defn.website}")
    print(f"  Docs / API keys: {defn.docs_url}")
    print(f"  Env var: {defn.env_key}")
    print(f"  API format: {defn.api_type}")
    print(f"  Default model: {defn.default_model}")
    print(f"  Tier: {defn.tier}")
    if defn.notes:
        print(f"  Notes: {defn.notes}")
