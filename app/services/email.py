import logging

logger = logging.getLogger("app.email")


def send_magic_link_email(to_email: str, link: str) -> None:
    """Send the passwordless login link to a user.

    No email provider is wired up yet — this just logs the link so local
    dev/testing can proceed. Swap this out for a real provider (SES,
    Postmark, Resend, ...) before going live; callers only depend on this
    function's signature, not on how delivery happens.
    """
    logger.info("Magic link for %s: %s", to_email, link)
