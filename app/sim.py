"""Simulated receiver (the customer's webhook URL), in the same app (D22)."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_sim_slow_seconds

router = APIRouter(prefix="/sim")

_NAMES = {"always_succeed", "fail_twice", "always_fail", "slow"}


@router.post("/{name}")
async def sim(name: str, request: Request):
    if name not in _NAMES:
        raise HTTPException(status_code=404, detail="unknown destination")
    if name == "always_fail":
        return JSONResponse(status_code=500, content={"ok": False})
    if name == "fail_twice":
        attempt_no = int(request.headers.get("X-Attempt-No", "1"))
        if attempt_no <= 2:
            return JSONResponse(status_code=500, content={"ok": False})
    if name == "slow":
        await asyncio.sleep(get_sim_slow_seconds())
    return {"ok": True}
