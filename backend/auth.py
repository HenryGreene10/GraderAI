from typing import Any, Optional

from fastapi import Depends, Header, HTTPException

from .services.supabase_client import get_supabase


def _extract_user(resp: Any) -> Optional[Any]:
    if resp is None:
        return None
    if isinstance(resp, dict):
        if "user" in resp:
            return resp.get("user")
        data = resp.get("data")
        if isinstance(data, dict) and "user" in data:
            return data.get("user")

    user = getattr(resp, "user", None)
    if user is not None:
        return user

    data = getattr(resp, "data", None)
    if isinstance(data, dict) and "user" in data:
        return data.get("user")
    if data is not None:
        maybe_user = getattr(data, "user", None)
        if maybe_user is not None:
            return maybe_user

    return None


def _user_id(user: Any) -> Optional[str]:
    if user is None:
        return None
    if isinstance(user, dict):
        value = user.get("id")
    else:
        value = getattr(user, "id", None)
    if not value:
        return None
    return str(value)


def get_current_user(authorization: Optional[str] = Header(None)) -> Any:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    try:
        resp = sb.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = _extract_user(resp)
    if not _user_id(user):
        raise HTTPException(status_code=401, detail="Invalid token")

    return user


def get_current_user_id(user: Any = Depends(get_current_user)) -> str:
    user_id = _user_id(user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id
