from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import httpx, os

app = FastAPI()

# --- config ---
SECRET = os.getenv("RECAPTCHA_SECRET")  # set in Vercel env
ALLOWED = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "https://jackzhenguo.github.io"
).split(",") if o.strip()]
# -------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,       # e.g. "https://a.com,https://b.com"
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VerifyBody(BaseModel):
    token: str

@app.get("/")
async def root():
    return {"ok": True, "service": "endapi", "allowed": ALLOWED}

@app.get("/favicon.ico")
async def favicon_ico():
    return Response(status_code=204)

@app.get("/favicon.png")
async def favicon_png():
    return Response(status_code=204)

@app.options("/verify")
async def options_verify():
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
