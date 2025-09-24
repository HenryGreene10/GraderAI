# backend/deps.py
from typing import TYPE_CHECKING

# ---- Supabase client (guarded) ----
try:
    from supabase import create_client, Client  # type: ignore
except ModuleNotFoundError:  # test/offline mode
    create_client = None  # type: ignore
    class Client:  # minimal stub for type hints/tests
        ...

# ---- Postgrest exception (guarded) ----
try:
    from postgrest import APIError as PostgrestAPIError  # type: ignore
except ModuleNotFoundError:
    class PostgrestAPIError(Exception):
        ...

# Optional: keep pure type hints without importing at runtime
if TYPE_CHECKING:
    from supabase import Client as _Client  # noqa: F401
