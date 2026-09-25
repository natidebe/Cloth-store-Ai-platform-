"""Staff-facing actions the agent doesn't handle itself (resolve escalations,
retry stuck orders). Must verify the caller's Supabase JWT and store membership."""
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])
