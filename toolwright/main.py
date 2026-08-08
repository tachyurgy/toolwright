"""HTTP surface exposing the registry in MCP shape."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .contracts import to_mcp_result
from .tools import build_registry

app = FastAPI(title="Toolwright", docs_url="/api/docs")
WEB = Path(__file__).resolve().parent.parent / "web"
REGISTRY = build_registry()


class CallRequest(BaseModel):
    name: str
    arguments: dict | list | str | int | float | bool | None = None


@app.get("/up")
def up() -> dict:
    return {"status": "ok", "tools": len(REGISTRY.describe())}


@app.get("/api/tools")
def tools() -> dict:
    return {"tools": REGISTRY.describe()}


@app.post("/api/call")
def call(req: CallRequest) -> dict:
    result = REGISTRY.call(req.name, req.arguments if req.arguments is not None else {})
    return {"result": result.as_dict(), "mcp": to_mcp_result(result)}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()
