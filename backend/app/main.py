from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import me


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cuy Loyalty API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(me.router)
    return app


app = create_app()
