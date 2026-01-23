import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

from fastapi import Header, HTTPException

from .config import SUPABASE_JWT_SECRET


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def _verify_supabase_jwt(token: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if header.get("alg") != "HS256":
        raise HTTPException(status_code=401, detail="Unsupported token algorithm")

    if not SUPABASE_JWT_SECRET:
        raise HTTPException(status_code=500, detail="Supabase JWT secret not configured")

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected = hmac.new(
        SUPABASE_JWT_SECRET.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()

    try:
        signature = _b64url_decode(sig_b64)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    exp = payload.get("exp")
    if exp is not None:
        try:
            if time.time() > float(exp):
                raise HTTPException(status_code=401, detail="Token expired")
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="Invalid token")

    return payload


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    payload = _verify_supabase_jwt(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return str(user_id)
