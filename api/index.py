from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
import httpx, os

app = FastAPI()

# --- config via env ---
SECRET = os.getenv("RECAPTCHA_SECRET")  # set in Vercel env
# comma-separated list, e.g. "https://jackzhenguo.github.io,https://captcha-mcp-research.vercel.app"
ALLOWED = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "https://jackzhenguo.github.io"
).split(",") if o.strip()]
# ----------------------

# Normal CORS middleware (covers most cases)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,       # or ["*"] for smoke tests (not with credentials)
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Safety net: attach CORS headers to every response that passes through FastAPI
@app.middleware("http")
async def ensure_cors_headers(request: Request, call_next):
    response = await call_next(request)
    origin = request.headers.get("Origin")
    if "*" in ALLOWED:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in ALLOWED:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    return response

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

# Explicit preflight handler for /verify (guarantees CORS headers)
@app.options("/verify")
async def options_verify(request: Request):
    origin = request.headers.get("Origin", "")
    headers = {
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }
    if "*" in ALLOWED:
        headers["Access-Control-Allow-Origin"] = "*"
        return PlainTextResponse(status_code=204, headers=headers)
    if origin in ALLOWED:
        headers["Access-Control-Allow-Origin"] = origin
        return PlainTextResponse(status_code=204, headers=headers)
    return PlainTextResponse("Origin not allowed", status_code=403, headers=headers)

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
    return JSONResponse({
        "success": r.get("success", False),
        "score": r.get("score"),
        "action": r.get("action"),
        "hostname": r.get("hostname"),
        "reason": ",".join(r.get("error-codes", [])) if not r.get("success") else "",
    })
