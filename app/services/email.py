import logging

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger("app.email")

# Lazily constructed, same reasoning as app.services.resume_storage's client:
# building it resolves credentials immediately, which would break importing
# this module (and anything that imports it) for anyone without SES
# credentials configured yet — e.g. local dev, which falls back to logging.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "sesv2",
            region_name=settings.email_sender_region,
            aws_access_key_id=settings.email_sender_access_key_id,
            aws_secret_access_key=settings.email_sender_secret_access_key,
        )
    return _client


def send_magic_link_email(to_email: str, link: str) -> None:
    """Send the passwordless login link to a user.

    Falls back to logging the link instead of sending when no SES
    credentials are configured, so local dev/testing works without any AWS
    setup.
    """
    if settings.email_sender_access_key_id is None:
        logger.info("Magic link for %s: %s", to_email, link)
        return

    try:
        _get_client().send_email(
            FromEmailAddress=settings.email_from_address,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    "Subject": {"Data": "Your Yabot Jobs login link"},
                    "Body": {
                        "Text": {"Data": f"Click to log in (expires in {settings.magic_link_ttl_minutes} minutes):\n\n{link}"},
                        "Html": {"Data": f'<p><a href="{link}">Click here to log in</a> (expires in {settings.magic_link_ttl_minutes} minutes).</p>'},
                    },
                }
            },
        )
    except ClientError:
        logger.exception("Failed to send magic link email to %s", to_email)
        raise
