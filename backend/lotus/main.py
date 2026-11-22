"""FastAPI entrypoint for Lotus AI Health.

    uvicorn lotus.main:app --reload            (from backend/)

Serves the API under /api and the built console (frontend/dist) at /.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .api.routes import router
from .config import settings
from .container import Container
from .sim.feed import Feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = Container()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as checkpointer:
        container.attach_checkpointer(checkpointer)
        feed = Feed(container, seed=settings.seed)
        app.state.container, app.state.feed = container, feed
        await feed.seed()
        if settings.simulator_enabled:
            feed.start()
        yield
        await feed.stop()


app = FastAPI(title="Lotus AI Health", version="0.1.0", lifespan=lifespan)
app.include_router(router)

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
