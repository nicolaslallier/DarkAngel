from fastapi import APIRouter

from app.api.routes import files, folders, health, me

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
# Protected routes take the `Claims` dependency (app.core.auth); health stays public.
api_router.include_router(me.router)
api_router.include_router(files.router)
api_router.include_router(folders.router)
