#!/usr/bin/env python3
"""GCS MCP Server for Sudo CLI.

Exposes Google Cloud Storage operations as MCP tools.
Reads configuration from ~/sudo-config.json automatically.

Tools exposed:
  - gcs_list          List files/objects in the bucket under a prefix
  - gcs_read          Read text content of a GCS object
  - gcs_write         Write text content to a GCS object
  - gcs_upload        Upload a local file or folder to GCS recursively
  - gcs_delete        Delete an object from GCS
  - gcs_list_buckets  List all buckets accessible to the credentials

Run standalone:
  python gcs_mcp_server.py
? 
Or configure in sudo via /mcp (see README).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ── Config resolution ─────────────────────────────────────────────────────────

def _load_sudo_config() -> dict:
    config_file = Path.home() / "sudo-config.json"
    if config_file.exists():
        try:
            return json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _get_gcs_client():
    """Return (storage.Client, bucket_name) using sudo-config.json or env vars."""
    try:
        from google.cloud import storage
    except ImportError:
        raise RuntimeError(
            "google-cloud-storage is not installed. Run: pip install google-cloud-storage"
        )

    cfg = _load_sudo_config()
    bucket_name = cfg.get("gcs_bucket") or os.environ.get("GCS_BUCKET", "")
    key_file = cfg.get("gcs_key_file") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    if not bucket_name:
        raise ValueError(
            "GCS bucket not set. Run inside sudo: /gcs-config bucket <name>"
        )

    if key_file:
        key_path = os.path.expanduser(key_file)
        client = storage.Client.from_service_account_json(key_path)
    else:
        # Fall back to Application Default Credentials (gcloud auth application-default login)
        client = storage.Client()

    return client, bucket_name


# ── MCP Server ────────────────────────────────────────────────────────────────

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gcs", description="Google Cloud Storage tools for Sudo CLI")


@mcp.tool()
def gcs_list(prefix: str = "") -> str:
    """List files in the configured GCS bucket under the given prefix.

    Args:
        prefix: Optional path prefix to filter results (e.g. 'projects/myapp/')
    """
    try:
        client, bucket_name = _get_gcs_client()
        bucket = client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix or None))
        if not blobs:
            return f"No files found in gs://{bucket_name}/{prefix}"
        lines = [f"Files in gs://{bucket_name}/{prefix or '(root)'}:"]
        for b in blobs:
            size_kb = (b.size or 0) / 1024
            lines.append(f"  {b.name}  ({size_kb:.1f} KB)")
        return "\n".join(lines)
    except Exception as e:
        return f"[GCS Error] {e}"


@mcp.tool()
def gcs_read(path: str) -> str:
    """Read text content of a file from the configured GCS bucket.

    Args:
        path: Full object path inside the bucket (e.g. 'projects/myapp/config.json')
    """
    try:
        client, bucket_name = _get_gcs_client()
        blob = client.bucket(bucket_name).blob(path)
        content = blob.download_as_text(encoding="utf-8")
        if len(content) > 8000:
            content = content[:8000] + "\n... [truncated at 8000 chars]"
        return f"[gs://{bucket_name}/{path}]\n{content}"
    except Exception as e:
        return f"[GCS Error] {e}"


@mcp.tool()
def gcs_write(path: str, content: str) -> str:
    """Write text content to a file in the configured GCS bucket.

    Args:
        path:    Full object path inside the bucket (e.g. 'notes/todo.md')
        content: Text content to write
    """
    try:
        client, bucket_name = _get_gcs_client()
        blob = client.bucket(bucket_name).blob(path)
        blob.upload_from_string(content, content_type="text/plain")
        return f"Written {len(content)} chars to gs://{bucket_name}/{path}"
    except Exception as e:
        return f"[GCS Error] {e}"


@mcp.tool()
def gcs_upload(local_path: str, gcs_prefix: str = "") -> str:
    """Upload a local file or entire folder recursively to the GCS bucket.

    Args:
        local_path: Absolute or relative local path to a file or directory
        gcs_prefix: Destination prefix inside the bucket (e.g. 'backups/myproject')
    """
    try:
        client, bucket_name = _get_gcs_client()
        local = Path(os.path.expanduser(local_path)).resolve()

        if not local.exists():
            return f"[GCS Error] Local path does not exist: {local}"

        bucket = client.bucket(bucket_name)
        prefix = gcs_prefix.rstrip("/")
        count = 0

        if local.is_file():
            dest = f"{prefix}/{local.name}" if prefix else local.name
            bucket.blob(dest).upload_from_filename(str(local))
            return f"Uploaded {local} → gs://{bucket_name}/{dest}"

        # Directory — walk recursively
        results = []
        for f in local.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(local).as_posix()
            dest = f"{prefix}/{rel}" if prefix else rel
            bucket.blob(dest).upload_from_filename(str(f))
            results.append(f"  ✓ {dest}")
            count += 1

        summary = f"Uploaded {count} files from {local} → gs://{bucket_name}/{prefix or '(root)'}"
        if results:
            summary += "\n" + "\n".join(results[:30])
            if count > 30:
                summary += f"\n  ... and {count - 30} more"
        return summary
    except Exception as e:
        return f"[GCS Error] {e}"


@mcp.tool()
def gcs_delete(path: str) -> str:
    """Delete a file/object from the configured GCS bucket.

    Args:
        path: Full object path inside the bucket to delete
    """
    try:
        client, bucket_name = _get_gcs_client()
        blob = client.bucket(bucket_name).blob(path)
        blob.delete()
        return f"Deleted gs://{bucket_name}/{path}"
    except Exception as e:
        return f"[GCS Error] {e}"


@mcp.tool()
def gcs_list_buckets() -> str:
    """List all GCS buckets accessible with the configured credentials."""
    try:
        client, _ = _get_gcs_client()
        buckets = list(client.list_buckets())
        if not buckets:
            return "No buckets found (or no list-buckets permission)."
        lines = ["Accessible GCS buckets:"]
        for b in buckets:
            lines.append(f"  • {b.name}  (location: {b.location})")
        return "\n".join(lines)
    except Exception as e:
        return f"[GCS Error] {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
