"""Rank the verified caller's channel posts under the seeded product settings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from codegen_kit_ranking.models import Publication, RankingSettings, TopPost, TopSettings
from codegen_kit_ranking.scoring import rank

UNREPORTED_VIEWS = "?"


def top_posts(posts: Sequence[Any], settings: TopSettings, now: datetime) -> list[TopPost]:
    """Return up to ``top_count`` provider posts, highest score first.

    ``posts`` carry ``channel``, ``date`` (ISO 8601 with offset), ``text``, ``url`` and
    ``views``, as ``tg-channels.posts.v1`` returns them; the url identifies a post.
    """

    by_url = {post.url: post for post in posts}
    publications = [
        Publication(
            id=post.url,
            date=datetime.fromisoformat(post.date),
            channel=post.channel,
            url=post.url,
            text=post.text,
            views=post.views,
        )
        for post in posts
    ]
    ranked = rank(
        publications,
        RankingSettings(weights=settings.weights, keywords=settings.keywords),
        limit=settings.top_count,
        now=now,
    )
    result = []
    for item in ranked:
        post = by_url[item.publication.id]
        views = UNREPORTED_VIEWS if post.views is None else str(post.views)
        result.append(
            TopPost(
                channel=post.channel,
                date=post.date,
                text=post.text,
                url=post.url,
                views=views,
                score=item.score,
            )
        )
    return result
