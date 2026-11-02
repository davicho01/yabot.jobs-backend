"""Push notifications to the mobile app through the Expo Push API, which
fronts APNs and FCM so this backend holds no Apple/Google push credentials.
Best-effort: a failed push is logged and never breaks the email alert sweep.
"""

import logging
import uuid

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.device_token import DeviceToken

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# Expo accepts up to 100 messages per request.
BATCH_SIZE = 100


def register_device(db: Session, user_id: uuid.UUID, token: str, platform: str) -> DeviceToken:
    """Upserts by token, so the same phone signing in as another user moves
    the token to them."""
    device = db.scalar(select(DeviceToken).where(DeviceToken.token == token))
    if device is None:
        device = DeviceToken(user_id=user_id, token=token, platform=platform)
        db.add(device)
    else:
        device.user_id = user_id
        device.platform = platform
    db.flush()
    return device


def unregister_device(db: Session, user_id: uuid.UUID, token: str) -> None:
    db.execute(delete(DeviceToken).where(DeviceToken.user_id == user_id, DeviceToken.token == token))


def send_push(db: Session, user_id: uuid.UUID, title: str, body: str, data: dict | None = None) -> int:
    """Pushes to every device the user has registered; returns how many
    messages Expo accepted. Tokens Expo says are dead (DeviceNotRegistered)
    are deleted."""
    tokens = list(db.scalars(select(DeviceToken.token).where(DeviceToken.user_id == user_id)))
    accepted = 0
    for start in range(0, len(tokens), BATCH_SIZE):
        batch = tokens[start : start + BATCH_SIZE]
        messages = [{"to": t, "title": title, "body": body, "data": data or {}, "sound": "default"} for t in batch]
        try:
            response = httpx.post(EXPO_PUSH_URL, json=messages, timeout=10.0)
            response.raise_for_status()
            tickets = response.json().get("data", [])
        except Exception:
            logger.exception("Expo push request failed for user %s.", user_id)
            continue
        for token, ticket in zip(batch, tickets):
            if ticket.get("status") == "ok":
                accepted += 1
            elif (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                db.execute(delete(DeviceToken).where(DeviceToken.token == token))
            else:
                logger.warning("Expo push rejected for user %s: %s", user_id, ticket.get("message"))
    return accepted
