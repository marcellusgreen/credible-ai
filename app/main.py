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

from app.api.routes import router as api_router
from app.api.primitives import router as primitives_router
from app.core.config import get_settings
from app.core.cache import check_rate_limit, DEFAULT_RATE_LIMIT, DEFAULT_RATE_WINDOW
from app.core.monitoring import record_request, record_rate_limit_hit

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
    """Log all requests with timing and record metrics."""
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

    # Record metrics for analytics (non-blocking)
    # Get client IP for analytics
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    # Fire and forget - don't await to avoid slowing down response
    import asyncio
    asyncio.create_task(record_request(
        path=request.url.path,
        method=request.method,
        status_code=response.status_code,
        duration_ms=duration_ms,
        client_ip=client_ip,
    ))

    response.headers["X-Request-ID"] = request_id
    return response


# Rate limiting middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting based on client IP."""
    # Skip rate limiting for health checks and docs
    path = request.url.path
    if path in ["/", "/docs", "/redoc", "/openapi.json", "/v1/ping", "/v1/health"]:
        return await call_next(request)

    # Get client identifier (IP address)
    # Check X-Forwarded-For for requests behind proxy/load balancer
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    # Check rate limit
    allowed, remaining, reset = await check_rate_limit(client_ip)

    if not allowed:
        logger.warning(
            "rate_limit_exceeded",
            client_ip=client_ip,
            path=path,
        )
        # Record rate limit hit for monitoring (fire and forget)
        import asyncio
        asyncio.create_task(record_rate_limit_hit(client_ip))
        return ORJSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded. Try again in {reset} seconds.",
                }
            },
            headers={
                "X-RateLimit-Limit": str(DEFAULT_RATE_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset),
                "Retry-After": str(reset),
            },
        )

    response = await call_next(request)

    # Add rate limit headers to successful responses
    response.headers["X-RateLimit-Limit"] = str(DEFAULT_RATE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset)

    return response


# Include Primitives API first (takes precedence for /companies, /bonds, /pricing)
app.include_router(primitives_router, prefix="/v1")

# Include legacy API routes (company-specific endpoints like /companies/{ticker}/debt)
app.include_router(api_router, prefix="/v1")


# Root redirect to docs
@app.get("/", include_in_schema=False)
async def root():
    """Redirect to API documentation."""
    return {
        "name": "DebtStack.ai",
        "description": "The credit API for AI agents",
        "version": settings.api_version,
        "docs": "/docs",
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
