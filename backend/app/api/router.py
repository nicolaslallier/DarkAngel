from fastapi import APIRouter

from app.api.routes import health, me

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
# Protected routes take the `Claims` dependency (app.core.auth); health stays public.
api_router.include_router(me.router)
