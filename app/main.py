"""
DebtStack.ai - The credit API for AI agents

Main FastAPI application entry point.
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
import structlog

from strawberry.fastapi import GraphQLRouter

from app.api.routes import router as api_router
from app.api.primitives import router as primitives_router
from app.core.config import get_settings
from app.graphql import schema as graphql_schema

settings = get_settings()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan manager."""
    # Startup
    logger.info("Starting DebtStack.ai API", version=settings.api_version)
    yield
    # Shutdown
    logger.info("Shutting down DebtStack.ai API")


# Create FastAPI app
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# Request logging middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log all requests with timing."""
    import time
    import uuid

    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()

    response = await call_next(request)

    duration_ms = (time.perf_counter() - start) * 1000

    logger.info(
        "request",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )

    response.headers["X-Request-ID"] = request_id
    return response


# Include API routes (legacy endpoints)
app.include_router(api_router, prefix="/v1")

# Include Primitives API (new endpoints optimized for agents)
app.include_router(primitives_router, prefix="/v1")

# Include GraphQL endpoint
graphql_app = GraphQLRouter(graphql_schema)
app.include_router(graphql_app, prefix="/graphql")


# Root redirect to docs
@app.get("/", include_in_schema=False)
async def root():
    """Redirect to API documentation."""
    return {
        "name": "DebtStack.ai",
        "description": "The credit API for AI agents",
        "version": settings.api_version,
        "docs": "/docs",
        "graphql": "/graphql",
        "primitives": {
            "companies": "/v1/companies",
            "bonds": "/v1/bonds",
            "pricing": "/v1/pricing",
            "resolve": "/v1/bonds/resolve",
            "traverse": "/v1/entities/traverse",
        },
        "system": {
            "health": "/v1/health",
            "status": "/v1/status",
        },
    }


# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return ORJSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "not_found",
                "message": str(exc.detail) if hasattr(exc, "detail") else "Not found",
            }
        },
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.error("internal_error", error=str(exc))
    return ORJSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An internal error occurred",
            }
        },
    )
