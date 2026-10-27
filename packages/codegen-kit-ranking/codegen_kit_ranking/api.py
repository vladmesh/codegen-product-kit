"""HTTP surface of the ranking package; every route acts only for the core-verified caller."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from typing import Annotated

from codegen_kit import caller_identity
from codegen_kit_tg_channels.posts import ActionError, recent_posts
from fastapi import APIRouter, Depends, HTTPException, Request

from codegen_kit_ranking.models import RankingInputError, RankingRequest, RankingResponse, TopPost
from codegen_kit_ranking.scoring import rank
from codegen_kit_ranking.store import NotConfiguredError, SettingsStore
from codegen_kit_ranking.top import top_posts

CONFLICT = 409
UNPROCESSABLE = 422

router = APIRouter()
CallerRef = Annotated[str, Depends(caller_identity)]
store = SettingsStore()


def _action_error(code: str) -> HTTPException:
    return HTTPException(status_code=CONFLICT, detail={"code": code})


class _NonFiniteNumberError(ValueError):
    pass


def _refuse_constant(literal: str) -> float:
    raise _NonFiniteNumberError(literal)


def _finite_float(literal: str) -> float:
    value = float(literal)
    if not math.isfinite(value):
        raise _NonFiniteNumberError(literal)
    return value


async def finite_json(request: Request) -> None:
    """Answer NaN, Infinity and overflowing numbers such as 1e309 with 422 before validation.

    The framework's validation error echoes the input, and a non-finite float cannot be
    serialised into that answer.
    """

    try:
        json.loads(await request.body(), parse_constant=_refuse_constant, parse_float=_finite_float)
    except _NonFiniteNumberError as error:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail={
                "code": "non_finite_number",
                "field": None,
                "clarify": f"{error} is not a finite number; send a finite number",
            },
        ) from None
    except json.JSONDecodeError:
        # Malformed JSON is the framework's own 422.
        return


@router.post(
    "/rank",
    response_model=RankingResponse,
    dependencies=[Depends(caller_identity), Depends(finite_json)],
)
async def rank_publications(payload: RankingRequest) -> RankingResponse:
    """Rank the supplied publications under the supplied settings."""

    try:
        top = rank(
            payload.publications,
            payload.settings,
            limit=payload.limit,
            now=payload.now,
            freshness_half_life_hours=payload.freshness_half_life_hours,
        )
    except RankingInputError as error:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail={"code": error.code, "field": error.field, "clarify": error.detail},
        ) from None
    return RankingResponse(top=top)


@router.get("/top", response_model=list[TopPost])
async def top(user_ref: CallerRef) -> list[TopPost]:
    """Rank the caller's tg-channels posts under the seeded ``ranking.*`` product settings."""

    try:
        settings = await store.read()
    except NotConfiguredError:
        raise _action_error("not_configured") from None
    try:
        posts = await recent_posts(user_ref)
    except ActionError as error:
        raise _action_error(
            "not_configured" if error.code == "not_configured" else "try_later"
        ) from None
    try:
        return top_posts(posts, settings, datetime.now(UTC))
    except RankingInputError:
        # Seeded weights that are all zero rank by nothing: the product is not configured.
        raise _action_error("not_configured") from None
