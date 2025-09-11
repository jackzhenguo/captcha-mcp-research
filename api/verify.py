from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx, os

app = FastAPI()

SECRET = os.getenv("RECAPTCHA_SECRET")
ALLOWED = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "https://jackzhenguo.github.io,https://captcha-mcp-research.vercel.app"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,      # for a quick smoke test you can set ["*"] (keep allow_credentials=False)
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VerifyBody(BaseModel):
    token: str | None = None

@app.get("/")              # GET /api/verify
async def root():
    return {"ok": True, "service": "verify", "path": "/api/verify", "allowed": ALLOWED}

@app.post("/")             # POST /api/verify
async def verify(request: Request):
    token = None
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        data = await request.json()
        token = (data or {}).get("token")
    elif "application/x-www-form-urlencoded" in ct:
        form = await request.form()
        token = form.get("token")

    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    if not SECRET:
        raise HTTPException(status_code=500, detail="RECAPTCHA_SECRET not configured")

    remote_ip = request.client.host if getattr(request, "client", None) else None
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": SECRET, "response": token, "remoteip": remote_ip},
        )
    r = resp.json()
    return {
        "success": r.get("success", False),
        "score": r.get("score"),
        "action": r.get("action"),
        "hostname": r.get("hostname"),
        "reason": ",".join(r.get("error-codes", [])) if not r.get("success") else "",
    }

@app.get("/favicon.ico")
async def favicon_ico():
    return Response(status_code=204)
