import asyncio
import logging
import os
from .image_loader import MAX_BYTES
from .post_parser import parse_post, revision_key
from .recognizer import Recognizer
from .store import SheetStore

logger = logging.getLogger(__name__)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def image_attachments(message):
    return [
        a for a in message.attachments if a.filename.lower().endswith(IMAGE_SUFFIXES)
    ]


def target_post(message):
    return (
        bool(image_attachments(message))
        and parse_post(message.content, allow_missing_category=True) is not None
    )


def revision(message):
    # Compare only within the current event; this key is never saved.
    return revision_key(
        message.id, message.content, [a.id for a in image_attachments(message)], ""
    )


class AutoInputService:
    """Handle live posts/edits without stored jobs or background recovery."""

    def __init__(self, client, connect, channel_ids, mode=None):
        self.client = client
        self.channel_ids = set(channel_ids)
        self.mode = mode or os.getenv("FURNITURE_AUTO_INPUT_MODE", "off")
        if self.mode not in {"off", "dry_run", "write"}:
            raise ValueError("Invalid FURNITURE_AUTO_INPUT_MODE")
        self.store = SheetStore(connect, self.mode)
        self.recognizer = None
        self.lock = asyncio.Lock()

    async def submit(self, message):
        if (
            self.mode == "off"
            or message.author.bot
            or message.channel.id not in self.channel_ids
            or not target_post(message)
        ):
            return
        # Serialize live events; images, results and plans belong to this call only.
        async with self.lock:
            try:
                await self.process(message)
            except Exception as exc:
                # Do not log message text, images or recognition evidence.
                # Never repeat a possibly successful Sheets write automatically.
                logger.warning(
                    "Furniture event failed id=%s error=%s",
                    message.id,
                    type(exc).__name__,
                )

    async def process(self, message):
        current_revision = revision(message)
        post = parse_post(message.content, allow_missing_category=True)
        channel = self.client.get_channel(
            message.channel.id
        ) or await self.client.fetch_channel(message.channel.id)
        latest = await channel.fetch_message(message.id)
        if revision(latest) != current_revision:
            return
        post, reasons = await asyncio.to_thread(self.store.prepare, post)
        if post is None:
            logger.info("Furniture event skipped id=%s reasons=%s", message.id, reasons)
            return
        attachments = image_attachments(message)
        if len(attachments) > 10 or sum(a.size for a in attachments) > 40 * 1024 * 1024:
            raise ValueError("attachment total exceeds limit")
        images = []
        for attachment in attachments:
            if attachment.size > MAX_BYTES:
                raise ValueError("image exceeds limit")
            data = await asyncio.wait_for(attachment.read(), timeout=30)
            if len(data) > MAX_BYTES:
                raise ValueError("image exceeds limit")
            images.append(data)
        if self.recognizer is None:
            self.recognizer = await asyncio.to_thread(Recognizer)
        result = await asyncio.to_thread(
            self.recognizer.analyze, images, category=post["category"]
        )
        # The edit event handles changed posts. Do not create another job here.
        latest = await channel.fetch_message(message.id)
        if revision(latest) != current_revision:
            return
        outcome = await asyncio.to_thread(self.store.apply, post, result)
        logger.info(
            "Furniture event finished id=%s mode=%s state=%s fields=%d reasons=%s",
            message.id,
            self.mode,
            outcome["state"],
            len(outcome["plan"].values),
            outcome["plan"].reasons
            + ([outcome["reason"]] if outcome.get("reason") else []),
        )
