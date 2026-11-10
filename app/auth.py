from fastapi import Header, HTTPException
from app.config import settings

async def require_api_key(x_api_key: str = Header(...)):
    allowed = {k.strip() for k in settings.runner_api_keys.split(",") if k.strip()}
    if not allowed:
        raise HTTPException(500, "RUNNER_API_KEYS empty")
    if x_api_key not in allowed:
        raise HTTPException(401, "Invalid API key")
    return x_api_key