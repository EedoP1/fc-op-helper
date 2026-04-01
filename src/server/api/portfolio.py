"""Portfolio API — aggregates read and write sub-routers."""
from fastapi import APIRouter

from src.server.api.portfolio_read import router as read_router
from src.server.api.portfolio_write import router as write_router

router = APIRouter(prefix="/api/v1")
router.include_router(read_router)
router.include_router(write_router)
