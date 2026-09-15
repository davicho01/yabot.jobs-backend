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


# Mirrors the site's "Apple Bold" palette (src/index.css light theme) and
# its pill-button/plain-sans convention (Header.css) — email clients don't
# honor prefers-color-scheme reliably, so this is deliberately the light
# palette only, not a light+dark pair. No custom webfonts either: Outfit/
# Manrope aren't reliably loadable in email clients, so this leans on the
# system sans stack, which is close enough to Manrope/SF to read as the
# same family.
_MAGIC_LINK_HTML = """\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f7;padding:40px 16px;">
  <tr>
    <td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border:1px solid #e8e8ed;border-radius:18px;max-width:480px;width:100%;">
        <tr>
          <td style="padding:32px 32px 24px;">
            <table role="presentation" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:32px;">
                  <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAACXBIWXMAADsOAAA7DgHMtqGDAAACQElEQVRYhWNgQAMyMjK6MjLyE2Vk5K/Iysp/kZWV/08h/gIyS0ZGfoKMjIwOAy6goqLCLiMjN01WVv4vFSzFhf/IyspP0dLSYsOwXFZWbh8NLUbBMjLye1EcISMjN51eliPhyfA4p3Gw44kOWW0GUIIbAMuhUSHXB3LA1YFygKys/GUGWVn5z6RoklIz/S8jp0QlB8h9YiBFA59H63+Gwsf/WZMO/JeRV6aKIxiIVSitpPWfMfcW2AEgLGoaTl8HCDoUwi0HYR6/SfR1AGviPhQHMKef/S8rq0AfB4gbeKFYDsMSus70cQB34Awkix/B2fxudbRwgMJ/YZu0//wu1XCMnPh4ffvgbLb4nYhQ0vdA0YOOQWZiizIGdAEh63SswQ3LftLKOv8Z8u9DxAoe/pdSMfgvpWr0nzHvDk59MAwymyIHCNnngtWwx2xCiNlm/ed3qyVoOdEOkIVGAZ9XOwpGDkIB5zK4oZzhy/+zpJ+G87mC52HoRddPVjaURcKSWjZYEyVT1uX/MvIqtCkHZNEwS8oxjCDm9e6iTTaUxYJ5vbsxywUtO/o5QMzIH8VyUMIkxxwGch0gK6/8nyn7GtwBwtYpdHaALKh6bkGqnlXIdYDcJ0ocQUkDRUZG7uPAN8lkZOQnDJQDZGTkekHNch1oj4XeDvgjJyenBe4bgLpL9Pe9/ER4zwjUTZKRkd9DR8t3Gxsbs6L0D0GOAHWXaBwdf0A+x7AcGYC6S6AeCyiFktpnwIFBZlwGJTh4nCMBADZXj81ElYv0AAAAAElFTkSuQmCC" width="32" height="32" alt="Yabot Jobs" style="display:block;border-radius:8px;">
                </td>
                <td style="padding-left:10px;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:700;font-size:18px;letter-spacing:-0.01em;color:#1d1d1f;">Yabot Jobs</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 8px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:22px;font-weight:700;letter-spacing:-0.01em;color:#1d1d1f;">
            Your login link
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 24px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:14px;line-height:1.6;color:#6e6e73;">
            Click the button below to sign in. This link expires in {ttl} minutes and can only be used once.
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 28px;">
            <a href="{link}" style="display:inline-block;background-color:#0071e3;color:#ffffff;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:600;font-size:14px;text-decoration:none;padding:12px 24px;border-radius:980px;">
              Log in &rarr;
            </a>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 32px;border-top:1px solid #e8e8ed;padding-top:16px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:12px;line-height:1.6;color:#6e6e73;">
            Didn't request this? You can safely ignore this email.<br>
            If the button doesn't work, copy and paste this link: <a href="{link}" style="color:#0071e3;">{link}</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


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
                        "Html": {"Data": _MAGIC_LINK_HTML.format(link=link, ttl=settings.magic_link_ttl_minutes)},
                    },
                }
            },
        )
    except ClientError:
        logger.exception("Failed to send magic link email to %s", to_email)
        raise
