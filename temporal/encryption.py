"""
End-to-end payload encryption for the Security Scanner.

WHY THIS MATTERS:
By default, Temporal stores all workflow/activity inputs and outputs in plaintext
in its event history. For a security scanner that handles GitHub API tokens and
private repository data, that's a problem — anyone with access to the Temporal
server can read your tokens and see which repos failed security checks.

Temporal's PayloadCodec gives us client-side encryption: data is encrypted before
it leaves the worker/client and decrypted when it comes back. The Temporal server
only ever sees ciphertext. It doesn't need the key, doesn't know the algorithm,
and can't read your data. This is the same pattern used by fintech companies
running Temporal in production with PCI-DSS requirements.

ARCHITECTURE:
    Client/Worker → PayloadCodec.encode() → [encrypted bytes] → Temporal Server
    Temporal Server → [encrypted bytes] → PayloadCodec.decode() → Client/Worker

The key never leaves your infrastructure. The Temporal server is just storing
opaque bytes. You can verify this by looking at the Web UI after enabling
encryption — all you'll see is "binary/encrypted" payloads.

IMPORTANT SECURITY NOTES:
1. Search attributes are NOT encrypted (they bypass the codec). Never put
   sensitive data in search attributes.
2. By default, failure messages and stack traces are also stored in plaintext.
   To encrypt those too, configure the failure converter with
   encode_common_attributes=True (see worker.py for discussion of why
   we omit this in the demo).
3. The encryption key must be managed securely (env var, secrets manager, etc.).
   Rotating keys requires a versioned codec that can decrypt with old keys.

EXPERT NOTES (for security/crypto reviewers):
- Threat model: We protect against Temporal server/DB access (they see only
  ciphertext). We do NOT protect against compromised client/worker (they hold
  the key), key theft, or in-memory exposure. Transport security (TLS) is
  separate; use it in production.
- Authenticated encryption: Fernet is Encrypt-then-MAC (AES-128-CBC + HMAC-SHA256).
  Never use raw encryption without authentication; tampering could alter or leak
  plaintext. Fernet verifies the MAC on decrypt and fails on tampering.
- IV: Fernet embeds a unique IV per token (from its structured format). We do
  not reuse IVs. For production AES-GCM, use a unique nonce per encryption and
  never reuse under the same key.
- See ENCRYPTION.md for full "Expert perspective" (threat model, AE, Codec
  Server security, what encryption does not do).
"""

import os
from typing import List

from cryptography.fernet import Fernet
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

# Metadata key that marks a payload as encrypted.
# This tells the decoder which codec to apply on the way back.
ENCODING_KEY = b"binary/encrypted"


class EncryptionCodec(PayloadCodec):
    """
    AES-128-CBC encryption codec using Fernet (symmetric, authenticated).

    Fernet is built on AES-128-CBC with HMAC-SHA256 for authentication.
    It's the Python cryptography library's high-level recipe — hard to misuse,
    provides authenticated encryption, and includes a timestamp for key rotation.

    For production, you'd swap this for an envelope encryption pattern with
    AWS KMS / GCP KMS / HashiCorp Vault managing the key encryption key (KEK).
    The approach here keeps the demo self-contained while showing the pattern.
    """

    # Default key for local development ONLY.
    # Worker and starter run as separate processes — they need a shared key.
    # In dev mode, this well-known key lets everything work without config.
    # In production, always set TEMPORAL_ENCRYPTION_KEY.
    #
    # Generate a production key with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    _DEV_KEY = b"0YrF5gNrMXCbGLDYiLMT5ORxDdN7-U3GfHTrEMbIpiw="

    def __init__(self, key: bytes | None = None) -> None:
        """
        Initialize with an encryption key.

        Args:
            key: Fernet key (32 bytes, URL-safe base64 encoded).
                 If None, reads from TEMPORAL_ENCRYPTION_KEY env var.
                 If that's also unset, uses a hardcoded dev key (local only).
        """
        if key:
            self._fernet = Fernet(key)
        else:
            env_key = os.environ.get("TEMPORAL_ENCRYPTION_KEY")
            if env_key:
                self._fernet = Fernet(env_key.encode())
            else:
                # Dev mode: use a well-known key so worker and starter
                # (separate processes) can encrypt/decrypt each other's data.
                # This is NOT secure — it just lets the demo work without config.
                import logging
                logging.getLogger(__name__).warning(
                    "Using default dev encryption key. "
                    "Set TEMPORAL_ENCRYPTION_KEY for production."
                )
                self._fernet = Fernet(self._DEV_KEY)

    async def encode(self, payloads: List[Payload]) -> List[Payload]:
        """Encrypt each payload before it's sent to the Temporal server."""
        return [
            Payload(
                metadata={
                    "encoding": ENCODING_KEY,
                },
                data=self._fernet.encrypt(p.SerializeToString()),
            )
            for p in payloads
        ]

    async def decode(self, payloads: List[Payload]) -> List[Payload]:
        """Decrypt each payload received from the Temporal server."""
        result = []
        for p in payloads:
            # Only decrypt payloads we encrypted (check the encoding marker)
            if p.metadata.get("encoding") == ENCODING_KEY:
                decrypted = Payload()
                decrypted.ParseFromString(self._fernet.decrypt(p.data))
                result.append(decrypted)
            else:
                # Pass through payloads we didn't encrypt (e.g. before encryption was enabled).
                # Security: unencrypted payloads in history will be returned as plaintext; ensure
                # all writers use the codec in production, or consider failing on unknown encoding.
                result.append(p)
        return result
