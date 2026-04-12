import os
from threading import Thread

from fastapi import FastAPI
import uvicorn

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Server is Online."}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def get_port():
    port = os.environ.get("PORT", "8080")
    try:
        return int(port)
    except ValueError:
        return 8080


def start():
    uvicorn.run(app, host="0.0.0.0", port=get_port())


def server_thread():
    thread = Thread(target=start, daemon=True)
    thread.start()
