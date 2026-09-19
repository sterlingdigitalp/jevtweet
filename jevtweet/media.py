"""Replaceable media-description boundary; V1 accepts supplied text only."""

from typing import Protocol

from .contracts import EvidenceText, Media


class MediaDescriber(Protocol):
    async def describe(self, media: Media) -> EvidenceText | None: ...


class ManualMediaDescriber:
    async def describe(self, media: Media) -> EvidenceText | None:
        return media.description
