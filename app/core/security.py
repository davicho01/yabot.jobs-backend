import hashlib
import secrets

from cryptography.fernet import Fernet

from app.core.config import settings

# Current encryption key version. Bump this and extend `_fernet_for_version`
# when rotating keys so old ciphertext (tagged with its version) still decrypts.
CURRENT_ENCRYPTION_KEY_VERSION = 1


def _fernet_for_version(version: int) -> Fernet:
    if version != CURRENT_ENCRYPTION_KEY_VERSION:
        raise ValueError(f"Unknown encryption key version: {version}")
    return Fernet(settings.api_key_encryption_key.encode())


def encrypt_secret(plaintext: str) -> tuple[bytes, int]:
    """Encrypt a secret (e.g. an LLM API key) for storage.

    Returns (ciphertext, key_version) — persist both alongside each other.
    """
    fernet = _fernet_for_version(CURRENT_ENCRYPTION_KEY_VERSION)
    return fernet.encrypt(plaintext.encode()), CURRENT_ENCRYPTION_KEY_VERSION


def decrypt_secret(ciphertext: bytes, key_version: int) -> str:
    fernet = _fernet_for_version(key_version)
    return fernet.decrypt(ciphertext).decode()


def generate_token() -> str:
    """A high-entropy, URL-safe token for magic links / session cookies."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """One-way hash of a magic-link/session token for DB storage.

    We never store the raw token — only what's needed to verify a
    presented token via constant-time comparison against this hash.
    """
    return hashlib.sha256(token.encode()).hexdigest()
