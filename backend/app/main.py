"""FastAPI entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import documents, review


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LexIntake", version="0.1.0")

    # The browser calls the API from the Next.js origin, so it must be allowed explicitly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(documents.router)
    app.include_router(review.router)
    return app


app = create_app()
