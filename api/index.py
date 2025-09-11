from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx, os

app = FastAPI()

# --- config via env ---
SECRET = os.getenv("RECAPTCHA_SECRET")  # set in Vercel env
# comma-separated list, e.g. "https://jackzhenguo.github.io,https://your-site.vercel.app"
ALLOWED = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "https://jackzhenguo.github.io"
).split(",") if o.strip()]
# ----------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,       # or ["*"] for quick smoke tests
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VerifyBody(BaseModel):
    token: str

@app.get("/")
async def root():
    return {"ok": True, "service": "endapi", "allowed": ALLOWED}

# quiet browser icon fetches
@app.get("/favicon.ico")
async def favicon_ico():
    return Response(status_code=204)

@app.get("/favicon.png")
async def favicon_png():
    return Response(status_code=204)

# NOTE: no custom @app.options("/verify") handler here.
# CORSMiddleware will handle preflight automatically.

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
