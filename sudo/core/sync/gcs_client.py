"""Google Cloud Storage client wrapper with offline resilience.

Wraps the google-cloud-storage library with retry logic,
offline detection, and clean error handling.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Optional

try:
    from google.cloud import storage as gcs_storage
    from google.api_core.exceptions import (
        GoogleAPIError,
        NotFound,
        Forbidden,
        NetworkError,
    )
    GCS_AVAILABLE = True
except ImportError:
    GCS_AVAILABLE = False


class GCSOfflineError(Exception):
    """Raised when GCS is unreachable."""
    pass


class GCSCredentialsError(Exception):
    """Raised when GCS credentials are invalid or missing."""
    pass


class GCSClient:
    """GCS client with offline detection, retry, and clean error handling."""

    def __init__(self, bucket_name: str, credentials_path: Optional[str] = None):
        if not GCS_AVAILABLE:
            raise ImportError(
                "google-cloud-storage is required. "
                "Install with: pip install google-cloud-storage"
            )

        self.bucket_name = bucket_name
        self.credentials_path = credentials_path
        self._client = None
        self._bucket = None
        self._online_cache: Optional[bool] = None
        self._online_cache_time: float = 0
        self._connect()

    def _connect(self) -> None:
        """Establish connection to GCS."""
        try:
            if self.credentials_path:
                cred_path = os.path.expanduser(self.credentials_path)
                if not os.path.isfile(cred_path):
                    raise GCSCredentialsError(
                        f"Credentials file not found: {cred_path}"
                    )
                self._client = gcs_storage.Client.from_service_account_json(cred_path)
            else:
                # Try ADC (Application Default Credentials)
                self._client = gcs_storage.Client()

            self._bucket = self._client.bucket(self.bucket_name)
        except GCSCredentialsError:
            raise
        except Exception as e:
            raise GCSCredentialsError(f"Failed to connect to GCS: {e}")

    def is_online(self, force_check: bool = False) -> bool:
        """Check internet connectivity. Cached for 30 seconds."""
        now = time.time()
        if not force_check and self._online_cache is not None:
            if now - self._online_cache_time < 30:
                return self._online_cache

        try:
            # Lightweight check — try to access the bucket
            self._bucket.exists()
            self._online_cache = True
        except (NetworkError, ConnectionError, OSError, TimeoutError):
            self._online_cache = False
        except Exception:
            # Any other exception could mean network issues
            self._online_cache = False

        self._online_cache_time = now
        return self._online_cache

    def _retry(self, func, *args, max_retries: int = 3, **kwargs):
        """Execute a function with exponential backoff retry."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except NetworkError as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
            except Forbidden as e:
                raise GCSCredentialsError(
                    f"Permission denied. Check your GCS credentials and bucket permissions.\n{e}"
                )
            except NotFound:
                raise
        raise GCSOfflineError(
            f"Failed after {max_retries} attempts. Network may be offline.\n{last_error}"
        )

    def upload_file(
        self,
        local_path: Path,
        cloud_prefix: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Upload a single file to GCS."""
        if not self.is_online():
            return False

        rel_name = local_path.name
        blob_path = f"{cloud_prefix}/{rel_name}"
        blob = self._bucket.blob(blob_path)

        extra = {}
        if metadata:
            extra["metadata"] = metadata

        try:
            self._retry(blob.upload_from_filename, str(local_path), **extra)
            return True
        except (GCSOfflineError, NotFound, GCSCredentialsError):
            return False
        except Exception:
            return False

    def download_file(self, cloud_path: str, local_path: Path) -> bool:
        """Download a single file from GCS."""
        if not self.is_online():
            return False

        blob = self._bucket.blob(cloud_path)
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._retry(blob.download_to_filename, str(local_path))
            return True
        except (GCSOfflineError, NotFound, GCSCredentialsError):
            return False
        except Exception:
            return False

    def delete_file(self, cloud_path: str) -> bool:
        """Delete a file from GCS."""
        if not self.is_online():
            return False

        blob = self._bucket.blob(cloud_path)
        try:
            self._retry(blob.delete)
            return True
        except NotFound:
            return True  # Already gone
        except (GCSOfflineError, GCSCredentialsError):
            return False
        except Exception:
            return False

    def list_files(self, prefix: str) -> list[dict]:
        """List all files under a cloud prefix with metadata."""
        if not self.is_online():
            return []

        try:
            blobs = self._retry(self._list_blobs, prefix)
            results = []
            for blob in blobs:
                results.append({
                    "name": blob.name,
                    "size": blob.size,
                    "md5": blob.md5_hash,
                    "updated": blob.updated.isoformat() if blob.updated else None,
                    "metadata": blob.metadata or {},
                })
            return results
        except (GCSOfflineError, GCSCredentialsError):
            return []
        except Exception:
            return []

    def _list_blobs(self, prefix: str):
        """List blobs with a prefix (internal helper)."""
        return list(self._bucket.list_blobs(prefix=prefix))

    def get_file_metadata(self, cloud_path: str) -> Optional[dict]:
        """Get metadata for a single cloud file."""
        if not self.is_online():
            return None

        blob = self._bucket.blob(cloud_path)
        try:
            self._retry(blob.reload)
            return {
                "name": blob.name,
                "size": blob.size,
                "md5": blob.md5_hash,
                "updated": blob.updated.isoformat() if blob.updated else None,
                "metadata": blob.metadata or {},
            }
        except NotFound:
            return None
        except (GCSOfflineError, GCSCredentialsError):
            return None
        except Exception:
            return None

    def file_exists(self, cloud_path: str) -> bool:
        """Check if a file exists in GCS."""
        if not self.is_online():
            return False

        blob = self._bucket.blob(cloud_path)
        try:
            return self._retry(blob.exists)
        except (GCSOfflineError, GCSCredentialsError):
            return False
        except Exception:
            return False

    def get_bucket_info(self) -> dict:
        """Get bucket metadata."""
        if not self.is_online():
            return {"name": self.bucket_name, "status": "offline"}

        try:
            self._retry(self._bucket.reload)
            return {
                "name": self._bucket.name,
                "location": self._bucket.location,
                "storage_class": self._bucket.storage_class,
                "time_created": self._bucket.time_created.isoformat() if self._bucket.time_created else None,
            }
        except (GCSOfflineError, GCSCredentialsError):
            return {"name": self.bucket_name, "status": "offline"}
        except Exception:
            return {"name": self.bucket_name, "status": "error"}

    def compute_local_md5(self, path: Path) -> str:
        """Compute MD5 hash of a local file."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
