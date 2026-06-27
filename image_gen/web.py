"""FastAPI web UI: enter a match, see the generated + reviewed postcard."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config
from .client import OllamaImageClient
from .core import OllamaConnectionError, run_match

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="Match Postcard Generator")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=config.OUTPUT_DIR), name="outputs")

_client = OllamaImageClient()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.post("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    date: str = Form(...),
    team1: str = Form(...),
    team2: str = Form(...),
    game_type: str = Form(...),
    size: str = Form(config.DEFAULT_SIZE),
    count: int = Form(1),
) -> HTMLResponse:
    error: str | None = None
    result = None
    try:
        result = run_match(_client, date, team1, team2, game_type, size, config.OUTPUT_DIR, count)
    except (OllamaConnectionError, RuntimeError) as exc:
        error = str(exc)

    return templates.TemplateResponse(
        request, "result.html", {"result": result, "error": error}
    )