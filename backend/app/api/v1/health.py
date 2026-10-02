from fastapi import APIRouter

router = APIRouter(tags=["health"])


# HEAD too: uptime monitors (e.g. UptimeRobot) check with HEAD by default.
@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
