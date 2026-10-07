"""Actions always operate for the core-verified caller."""

from typing import Annotated

from codegen_kit import caller_identity
from fastapi import APIRouter, Depends, HTTPException

from codegen_kit_tg_channels.models import ChannelInput, ChannelView, PostView
from codegen_kit_tg_channels.service import ActionError, ChannelsService

router = APIRouter()
CallerRef = Annotated[str, Depends(caller_identity)]
_service: ChannelsService | None = None


def service() -> ChannelsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Channel service unavailable")
    return _service


@router.post("", response_model=ChannelView)
async def add_channel(payload: ChannelInput, user_ref: CallerRef) -> ChannelView:
    try:
        return await service().add(user_ref, payload.text)
    except ActionError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None


@router.get("", response_model=list[ChannelView])
async def list_channels(user_ref: CallerRef) -> list[ChannelView]:
    return await service().list(user_ref)


@router.get("/digest", response_model=list[PostView])
async def digest(user_ref: CallerRef) -> list[PostView]:
    try:
        return await service().digest(user_ref)
    except ActionError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None


@router.delete("/{id}", response_model=ChannelView)
async def remove_channel(id: str, user_ref: CallerRef) -> ChannelView:
    try:
        return await service().remove(user_ref, id)
    except ActionError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
