"""Minimal RS256 JWT signing for Google service account auth."""

import base64
import json
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def rs256_sign(header: dict[str, Any], payload: dict[str, Any], private_key_pem: str) -> str:
    """Create an RS256-signed JWT from header, payload, and PEM private key."""
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"
