from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import os

from bot import grid_engine, STATE_FILE

app = FastAPI()

# static web
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()

@app.get("/api/state")
async def get_state():
    if not os.path.exists(STATE_FILE):
        return JSONResponse({})
    with open(STATE_FILE) as f:
        return JSONResponse(json.load(f))

@app.on_event("startup")
async def startup():
    asyncio.create_task(grid_engine())
