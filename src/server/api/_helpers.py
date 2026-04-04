"""Shared helpers and request models for portfolio endpoints."""
from fastapi import Request
from pydantic import BaseModel, Field


def _read_session_factory(request: Request):
    """Return the read-only session factory if available, else the default one."""
    return getattr(request.app.state, "read_session_factory", None) or request.app.state.session_factory


# ── Request models ─────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    """Request body for POST /portfolio/generate."""

    budget: int = Field(..., gt=0, description="Total budget in coins")
    banned_ea_ids: list[int] = Field(
        default_factory=list,
        description="EA IDs to exclude from portfolio generation",
    )


class ConfirmPlayer(BaseModel):
    """A single player entry in a confirm request."""

    ea_id: int
    buy_price: int
    sell_price: int


class ConfirmRequest(BaseModel):
    """Request body for POST /portfolio/confirm."""

    players: list[ConfirmPlayer]


class SwapPreviewRequest(BaseModel):
    """Request body for POST /portfolio/swap-preview."""

    freed_budget: int = Field(..., gt=0, description="Budget freed by removing a player")
    excluded_ea_ids: list[int]
    current_count: int = Field(
        ...,
        ge=0,
        description=(
            "Draft player count AFTER the removed player has been spliced out. "
            "The server uses this to compute how many replacement slots are needed: "
            "needed = TARGET_PLAYER_COUNT - current_count. Passing the post-splice "
            "count means rapid sequential removals each report the correct draft size "
            "and receive the right number of replacements."
        ),
    )


class RebalanceRequest(BaseModel):
    """Request body for POST /portfolio/rebalance."""

    budget: int = Field(..., gt=0, description="Total budget in coins")
