from fastapi import APIRouter

from app.api.routes import costs as cost_routes
from app.api.routes import (
    files,
    folders,
    health,
    household,
    infra,
    invoices,
    me,
    metrics,
    providers,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
# Protected routes take the `Claims` dependency (app.core.auth); health stays public.
api_router.include_router(me.router)
api_router.include_router(files.router)
api_router.include_router(folders.router)
api_router.include_router(household.router)
api_router.include_router(providers.router)
api_router.include_router(invoices.router)
api_router.include_router(cost_routes.router)
api_router.include_router(infra.router)
api_router.include_router(metrics.router)
