import logging
from typing import Optional

from ..config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from ..deps import Client, create_client

logger = logging.getLogger(__name__)

_supabase: Optional[Client] = None


def set_supabase(client: Optional[Client]) -> None:
    global _supabase
    _supabase = client


def get_supabase() -> Optional[Client]:
    global _supabase
    if _supabase is not None:
        return _supabase
    if not create_client or not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        logger.warning("Supabase client unavailable; missing URL or service role key.")
        return None
    try:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as exc:
        logger.warning("Supabase client init failed: %s", exc)
        _supabase = None
    return _supabase
