# api/server.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx, os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET = "6LebX74rAAAAAPKIvCwdLvyZgdis2bS4NQ_1QYci"

class VerifyBody(BaseModel):
    token: str

@app.get("/")
async def root():
    return {"ok": True, "service": "endapi"}

@app.get("/favicon.ico")
async def favicon():
    return {"ok": True}

@app.post("/verify")
async def verify(body: VerifyBody, request: Request):
    if not body.token:
        raise HTTPException(status_code=400, detail="Missing token")
    if not SECRET:
        raise HTTPException(status_code=500, detail="RECAPTCHA_SECRET not configured")

    remote_ip = request.client.host if getattr(request, "client", None) else None

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": SECRET, "response": body.token, "remoteip": remote_ip},
        )
    result = resp.json()
    return {
        "success": result.get("success", False),
        "score": result.get("score"),
        "action": result.get("action"),
        "hostname": result.get("hostname"),
        "reason": ",".join(result.get("error-codes", [])) if not result.get("success") else "",
    }
