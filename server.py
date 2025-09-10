# server.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx, os

app = FastAPI()

# 仅用于本地调试：允许任意来源、方法、头
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 如果用 file:// 打开页面，origin 为 "null"，用 * 最省事
    allow_credentials=False,      # 如果你要带 cookie/凭证，这里必须 False 才能配合 "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET = os.getenv("RECAPTCHA_SECRET", "your_secret_key")

class VerifyBody(BaseModel):
    token: str

@app.post("/verify")
async def verify(body: VerifyBody, request: Request):
    print("begin to verify by fastapi server")
    if not body.token:
        raise HTTPException(status_code=400, detail="Missing token")
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": SECRET, "response": body.token, "remoteip": request.client.host},
        )
    result = r.json()
    return {
        "success": result.get("success", False),
        "score": result.get("score"),
        "action": result.get("action"),
        "hostname": result.get("hostname"),
        "reason": ",".join(result.get("error-codes", [])) if not result.get("success") else "",
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
