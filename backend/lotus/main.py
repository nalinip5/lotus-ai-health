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

from .api.auth_routes import auth, team
from .api.routes import public, router
from .api.visit_routes import ingest, visits
from .auth import LoginLimiter, UserStore
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
        app.state.users = UserStore(container.store)
        app.state.users.bootstrap()
        app.state.login_limiter = LoginLimiter()
        await feed.seed()
        if settings.simulator_enabled:
            feed.start()
        yield
        await feed.stop()


app = FastAPI(title="Lotus AI Health", version="0.1.0", lifespan=lifespan)
for r in (public, auth, team, ingest, visits, router):
    app.include_router(r)

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        # The shell must not be cached, so browsers pick up new hashed bundles right after a deploy.
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
