from fastapi import FastAPI
app = FastAPI()

@app.get("/")
async def ping():
    return {"pong": True}
