"""Encrypted configuration storage for API keys and sensitive data.

Uses Fernet symmetric encryption (AES-128) with PBKDF2 key derivation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


# Paths
STATE_DIR_BASE = Path.home() / ".config" / "sudo" / "state"
ENCRYPTED_CONFIG_FILE = STATE_DIR_BASE / "config.enc"
SALT_FILE = STATE_DIR_BASE / "config.salt"


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive encryption key from password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return kdf.derive(password.encode())


def _get_or_create_salt() -> bytes:
    """Get existing salt or create new one."""
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = os.urandom(16)
    STATE_DIR_BASE.mkdir(parents=True, exist_ok=True)
    SALT_FILE.write_bytes(salt)
    return salt


def _get_encryption_key(password: Optional[str] = None) -> bytes:
    """Get or create the Fernet encryption key."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError(
            "cryptography library not installed. Run: pip install cryptography"
        )
    
    salt = _get_or_create_salt()
    
    # Try to get password from environment or use a default
    if password is None:
        password = os.environ.get("SUDO_CONFIG_PASSWORD", "sudo-default-key-change-me")
    
    key = _derive_key(password, salt)
    # Fernet key must be 32 url-safe base64-encoded bytes
    import base64
    return base64.urlsafe_b64encode(key)


def _get_fernet(password: Optional[str] = None) -> Fernet:
    """Get Fernet instance for encryption/decryption."""
    key = _get_encryption_key(password)
    return Fernet(key)


def encrypt_config(config_dict: dict, password: Optional[str] = None) -> bytes:
    """Encrypt configuration dict to bytes."""
    f = _get_fernet(password)
    json_data = json.dumps(config_dict, separators=(",", ":")).encode()
    return f.encrypt(json_data)


def decrypt_config(encrypted_data: bytes, password: Optional[str] = None) -> dict:
    """Decrypt configuration bytes to dict."""
    f = _get_fernet(password)
    decrypted = f.decrypt(encrypted_data)
    return json.loads(decrypted.decode())


def save_encrypted_config(config_dict: dict, password: Optional[str] = None) -> None:
    """Save config dict to encrypted file."""
    STATE_DIR_BASE.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt_config(config_dict, password)
    ENCRYPTED_CONFIG_FILE.write_bytes(encrypted)


def load_encrypted_config(password: Optional[str] = None) -> Optional[dict]:
    """Load config from encrypted file."""
    if not ENCRYPTED_CONFIG_FILE.exists():
        return None
    try:
        encrypted = ENCRYPTED_CONFIG_FILE.read_bytes()
        return decrypt_config(encrypted, password)
    except Exception:
        return None


def is_encrypted() -> bool:
    """Check if encrypted config exists."""
    return ENCRYPTED_CONFIG_FILE.exists()