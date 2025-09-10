from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import httpx, os

app = FastAPI()

# allow your GitHub Pages origin (recommended) or "*"
ALLOWED = ["https://jackzhenguo.github.io"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,          # or ["*"]
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET = os.getenv("RECAPTCHA_SECRET")

class VerifyBody(BaseModel):
    token: str

@app.get("/")
async def root():
    return {"ok": True, "service": "endapi"}

@app.get("/favicon.ico")
async def favicon_ico():
    return Response(status_code=204)

@app.get("/favicon.png")
async def favicon_png():
    return Response(status_code=204)

@app.options("/verify")
async def options_verify():
    # FastAPI's CORSMiddleware normally handles this;
    # returning 204 explicitly avoids edge cases.
    return PlainTextResponse(status_code=204)

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
    r = resp.json()
    return {
        "success": r.get("success", False),
        "score": r.get("score"),
        "action": r.get("action"),
        "hostname": r.get("hostname"),
        "reason": ",".join(r.get("error-codes", [])) if not r.get("success") else "",
    }
