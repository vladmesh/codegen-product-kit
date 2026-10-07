"""Small product views and projections of the platform response contract."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ChannelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class ChannelView(BaseModel):
    id: str
    channel: str


class PostView(BaseModel):
    channel: str
    date: str
    text: str
    url: str


class ResolvedChannel(BaseModel):
    channel: str
    status: Literal["ok", "pending", "no_preview", "not_found", "not_a_channel", "error"]
    checked_at: AwareDatetime


class ChannelState(BaseModel):
    channel: str
    status: Literal["ok", "pending", "no_preview", "not_found", "not_a_channel", "error"]


class Post(BaseModel):
    channel: str
    id: int
    seq: int
    date: AwareDatetime
    text: str
    edited: bool
    deleted: bool

    def view(self) -> PostView:
        return PostView(
            channel=self.channel,
            date=self.date.isoformat(),
            text=self.text.strip()[:2500],
            url=f"https://t.me/{self.channel}/{self.id}",
        )


class PostPage(BaseModel):
    items: list[Post]
    next_cursor: str
    channels: list[ChannelState]
    has_more: bool = False
