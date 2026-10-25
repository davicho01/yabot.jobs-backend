import html
import logging
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings

if TYPE_CHECKING:
    from app.schemas.job import JobDetailRead

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


# Shared by every template below: the brand mark (the frontend's
# public/favicon.svg, a white pulse on the sunset-gradient tile) at 96px
# for 32px on high-density screens, with its rounded corners in the image
# itself for clients that ignore border-radius. Inlined as a data URI since
# email clients won't reliably fetch an external image.
_YABOT_LOGO_DATA_URI = (
    "data:image/png;base64,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"
    "ko/oYDIuMAAAAABJRU5ErkJggg=="
)

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
                  <img src="{logo}" width="32" height="32" alt="Yabot Jobs" style="display:block;border-radius:8px;">
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
                        "Html": {
                            "Data": _MAGIC_LINK_HTML.format(
                                logo=_YABOT_LOGO_DATA_URI, link=link, ttl=settings.magic_link_ttl_minutes
                            )
                        },
                    },
                }
            },
        )
    except ClientError:
        logger.exception("Failed to send magic link email to %s", to_email)
        raise


# The app's current look (the frontend's index.css tokens and LoginPage.css):
# the page's light grey, the header's brand row (the mark and "Yabot Jobs" in
# Inter 600, ink on the page, no colored band), then the login card: white,
# 24px corners, the card shadow with the sign-in page's faint magenta glow,
# its black "Sign-in" tab, and the code itself as the one big moment on a
# --chip fill. The border is for clients that drop box-shadow.
#
# Phones offer an emailed code above the keyboard (iOS Mail's security-code
# autofill) when the code sits next to the word "code" as one unbroken run of
# digits, so it's never split into spans or spaced with characters (only CSS
# letter-spacing). The hidden first line is the inbox preview and
# notification text, saying it plainly too.
_LOGIN_CODE_HTML = """\
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">Your Yabot Jobs sign-in code is {code}.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f6f6f7;padding:32px 16px 40px;">
  <tr>
    <td align="center">
      <table role="presentation" width="440" cellpadding="0" cellspacing="0" style="max-width:440px;width:100%;">
        <tr>
          <td style="padding:0 4px 24px;">
            <table role="presentation" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:32px;">
                  <img src="{logo}" width="32" height="32" alt="Yabot Jobs" style="display:block;border-radius:8px;">
                </td>
                <td style="padding-left:10px;font-family:'Inter',-apple-system,'Segoe UI',sans-serif;font-weight:600;font-size:19px;letter-spacing:-0.02em;color:#0a0a0a;">Yabot Jobs</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="background-color:#ffffff;border:1px solid #ececef;border-radius:24px;box-shadow:0 12px 28px rgba(31,26,53,0.08),0 1px 3px rgba(31,26,53,0.06),0 24px 70px -32px rgba(236,0,140,0.4);">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:28px 32px 0;">
                  <span style="display:inline-block;background-color:#0a0a0a;color:#ffffff;font-family:'Inter',-apple-system,'Segoe UI',sans-serif;font-weight:600;font-size:12px;padding:5px 13px;border-radius:980px;">Sign-in</span>
                </td>
              </tr>
              <tr>
                <td style="padding:16px 32px 8px;font-family:'Inter',-apple-system,'Segoe UI',sans-serif;font-size:30px;line-height:1.2;font-weight:600;letter-spacing:-0.02em;color:#0a0a0a;">
                  Your sign-in code
                </td>
              </tr>
              <tr>
                <td style="padding:0 32px 24px;font-family:'Inter',-apple-system,'Segoe UI',sans-serif;font-size:15px;line-height:1.55;color:#5d5d66;">
                  Enter it where you asked for it, and you'll pick up right where you left off.
                </td>
              </tr>
              <tr>
                <td style="padding:0 32px 20px;">
                  <div style="background-color:#f0f0f3;border-radius:14px;padding:18px 0;text-align:center;font-family:ui-monospace,'SFMono-Regular','SF Mono',Menlo,Consolas,monospace;font-size:36px;font-weight:600;letter-spacing:10px;color:#0a0a0a;">{code}</div>
                </td>
              </tr>
              <tr>
                <td style="padding:0 32px 32px;font-family:'Inter',-apple-system,'Segoe UI',sans-serif;font-size:14px;line-height:1.55;color:#5d5d66;">
                  It expires in {ttl} minutes and works once.
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:20px 8px 0;font-family:'Inter',-apple-system,'Segoe UI',sans-serif;font-size:12px;line-height:1.6;color:#5d5d66;text-align:center;">
            Didn't ask for this? You can ignore this email; no one can sign in without the code.
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def send_login_code_email(to_email: str, code: str) -> None:
    """Send a six-digit sign-in code (see app.services.auth.create_login_code).
    Logs it instead when no SES credentials are configured, like
    send_magic_link_email."""
    if settings.email_sender_access_key_id is None:
        logger.info("Sign-in code for %s: %s", to_email, code)
        return

    ttl = settings.login_code_ttl_minutes
    try:
        _get_client().send_email(
            FromEmailAddress=settings.email_from_address,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    # The code up front, so it reads in a phone's notification.
                    "Subject": {"Data": f"{code} is your Yabot Jobs sign-in code"},
                    "Body": {
                        "Text": {"Data": f"Your Yabot Jobs sign-in code is {code}.\n\nIt expires in {ttl} minutes."},
                        "Html": {"Data": _LOGIN_CODE_HTML.format(logo=_YABOT_LOGO_DATA_URI, code=code, ttl=ttl)},
                    },
                }
            },
        )
    except ClientError:
        logger.exception("Failed to send sign-in code email to %s", to_email)
        raise


# One matching posting's row in the digest — title links straight to its
# /apply page (mirroring JobCaseFile's "View application"/"Evaluate Job"
# link), meta line is company + salary, whichever of those is known.
_DIGEST_ROW_HTML = """\
        <tr>
{logo_cell}          <td style="padding:14px 0;border-top:1px solid #e8e8ed;">
            <a href="{url}" style="display:block;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:700;font-size:15px;color:#1d1d1f;text-decoration:none;">{title}</a>
            <div style="font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;color:#6e6e73;margin-top:2px;">{meta}</div>
          </td>
        </tr>
"""

# The company's logo, beside its row — only for rows that have one (see
# app.services.company_logos); a row without one gets an empty spacer cell
# of the same width, so its text stays in the text column and lines up.
_DIGEST_LOGO_CELL_HTML = """\
          <td width="40" valign="top" style="width:40px;padding:16px 0 14px;border-top:1px solid #e8e8ed;">
            <img src="{src}" width="28" height="28" alt="{alt}" style="display:block;border-radius:6px;border:1px solid #e8e8ed;">
          </td>
"""

_DIGEST_LOGO_SPACER_HTML = """\
          <td width="40" style="width:40px;padding:0;border-top:1px solid #e8e8ed;">&nbsp;</td>
"""

_DIGEST_MORE_ROW_HTML = """\
        <tr>
          <td colspan="2" style="padding:14px 0;border-top:1px solid #e8e8ed;font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;">
            <a href="{board_url}" style="color:#0071e3;text-decoration:none;">+{more_count} more match{plural} &rarr;</a>
          </td>
        </tr>
"""

_DIGEST_HTML = """\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f7;padding:40px 16px;">
  <tr>
    <td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border:1px solid #e8e8ed;border-radius:18px;max-width:480px;width:100%;">
        <tr>
          <td style="padding:32px 32px 24px;">
            <table role="presentation" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:32px;">
                  <img src="{logo}" width="32" height="32" alt="Yabot Jobs" style="display:block;border-radius:8px;">
                </td>
                <td style="padding-left:10px;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:700;font-size:18px;letter-spacing:-0.01em;color:#1d1d1f;">Yabot Jobs</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 4px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:22px;font-weight:700;letter-spacing:-0.01em;color:#1d1d1f;">
            {headline}
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{rows}            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px 28px;">
            <a href="{board_url}" style="display:inline-block;background-color:#0071e3;color:#ffffff;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:600;font-size:14px;text-decoration:none;padding:12px 24px;border-radius:980px;">
              See all matches &rarr;
            </a>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 32px;border-top:1px solid #e8e8ed;padding-top:16px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:12px;line-height:1.6;color:#6e6e73;">
            You're getting this because you saved this search on Yabot Jobs. Delete the saved search to stop.
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def _job_url(job: "JobDetailRead") -> str:
    return f"{settings.frontend_base_url}/jobs/{job.url.id}/apply"


def _job_meta(job: "JobDetailRead") -> str:
    posting = job.posting
    assert posting is not None  # every match here came from a SUCCESS-scanned posting
    parts = [p for p in (posting.company_name, _format_salary(posting)) if p]
    return html.escape(" · ".join(parts)) if parts else "&nbsp;"


def _logo_cell(job: "JobDetailRead") -> str:
    posting = job.posting
    src = posting.company_logo_url
    if src is None:
        return _DIGEST_LOGO_SPACER_HTML
    return _DIGEST_LOGO_CELL_HTML.format(src=html.escape(src), alt=html.escape(posting.company_name or ""))


def _format_salary(posting) -> str | None:
    if not posting.salary_min and not posting.salary_max:
        return None
    currency = f"{posting.salary_currency} " if posting.salary_currency else ""
    if posting.salary_min and posting.salary_max:
        return f"{currency}{posting.salary_min:,}–{posting.salary_max:,}"
    return f"{currency}{(posting.salary_min or posting.salary_max):,}"


def send_saved_search_digest_email(
    to_email: str,
    *,
    name: str | None,
    matches: "list[JobDetailRead]",
    more_count: int,
    board_url: str,
) -> None:
    """A digest of new postings matching a saved search — see
    app.services.saved_search_alerts.sweep_saved_searches, the only caller.
    `matches` is already capped by that caller; `more_count` is how many
    more matched beyond the cap. Same log-only local-dev fallback as
    send_magic_link_email.
    """
    total = len(matches) + more_count
    plural = "" if total == 1 else "es"
    # Names that already start with a quote (e.g. '"engineer" · Remote') aren't
    # wrapped in another pair.
    quoted_name = name if name and name.startswith('"') else f'"{name}"'
    search_label = html.escape(quoted_name) if name else "your search"
    headline = f"{total} new match{plural} for {search_label}"
    subject = f"{total} new job match{plural} on Yabot Jobs" if not name else f"{total} new match{plural} for {quoted_name}"

    rows_html = "".join(
        _DIGEST_ROW_HTML.format(
            logo_cell=_logo_cell(job),
            url=_job_url(job),
            title=html.escape(job.posting.title or "Untitled role"),
            meta=_job_meta(job),
        )
        for job in matches
    )
    if more_count:
        rows_html += _DIGEST_MORE_ROW_HTML.format(
            board_url=board_url, more_count=more_count, plural="" if more_count == 1 else "es"
        )

    text_lines = [f"{headline}:", ""]
    for job in matches:
        posting = job.posting
        company = f" at {posting.company_name}" if posting.company_name else ""
        text_lines.append(f"- {posting.title or 'Untitled role'}{company}: {_job_url(job)}")
    if more_count:
        text_lines.append(f"...and {more_count} more match{'' if more_count == 1 else 'es'}.")
    text_lines += ["", f"See them all: {board_url}"]

    if settings.email_sender_access_key_id is None:
        logger.info("Saved-search digest for %s: %s", to_email, subject)
        return

    try:
        _get_client().send_email(
            FromEmailAddress=settings.email_from_address,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": "\n".join(text_lines)},
                        "Html": {
                            "Data": _DIGEST_HTML.format(
                                logo=_YABOT_LOGO_DATA_URI, headline=headline, rows=rows_html, board_url=board_url
                            )
                        },
                    },
                }
            },
        )
    except ClientError:
        logger.exception("Failed to send saved-search digest email to %s", to_email)
        raise


_FOLLOW_UP_HTML = """\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f7;padding:40px 16px;">
  <tr>
    <td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border:1px solid #e8e8ed;border-radius:18px;max-width:480px;width:100%;">
        <tr>
          <td style="padding:32px 32px 24px;">
            <table role="presentation" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:32px;">
                  <img src="{logo}" width="32" height="32" alt="Yabot Jobs" style="display:block;border-radius:8px;">
                </td>
                <td style="padding-left:10px;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:700;font-size:18px;letter-spacing:-0.01em;color:#1d1d1f;">Yabot Jobs</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 8px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:22px;font-weight:700;letter-spacing:-0.01em;color:#1d1d1f;">
            Time to follow up
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 24px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:14px;line-height:1.6;color:#6e6e73;">
            You set a reminder for <strong style="color:#1d1d1f;">{title}</strong>{company_clause}.
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 28px;">
            <a href="{apply_url}" style="display:inline-block;background-color:#0071e3;color:#ffffff;font-family:-apple-system,'Segoe UI',sans-serif;font-weight:600;font-size:14px;text-decoration:none;padding:12px 24px;border-radius:980px;">
              Open it &rarr;
            </a>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 32px;border-top:1px solid #e8e8ed;padding-top:16px;font-family:-apple-system,'Segoe UI',sans-serif;font-size:12px;line-height:1.6;color:#6e6e73;">
            You're getting this because you set a follow-up reminder on Yabot Jobs. Clear the date on that application to stop.
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def send_follow_up_reminder_email(to_email: str, *, title: str | None, company_name: str | None, apply_url: str) -> None:
    """A single reminder for one application whose follow_up_at is due — see
    app.services.follow_up_reminders.send_due_reminders, the only caller.
    Same log-only local-dev fallback as send_magic_link_email.
    """
    display_title = title or "this application"
    subject = f"Follow up on {display_title}" if title else "Time to follow up on a saved application"
    company_clause = f" at {company_name}" if company_name else ""

    if settings.email_sender_access_key_id is None:
        logger.info("Follow-up reminder for %s: %s%s", to_email, display_title, company_clause)
        return

    try:
        _get_client().send_email(
            FromEmailAddress=settings.email_from_address,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": f"Time to follow up on {display_title}{company_clause}:\n\n{apply_url}"},
                        "Html": {
                            "Data": _FOLLOW_UP_HTML.format(
                                logo=_YABOT_LOGO_DATA_URI,
                                title=html.escape(display_title),
                                company_clause=html.escape(company_clause),
                                apply_url=apply_url,
                            )
                        },
                    },
                }
            },
        )
    except ClientError:
        logger.exception("Failed to send follow-up reminder email to %s", to_email)
        raise


_FEEDBACK_KIND_LABELS = {"bug": "Bug report", "question": "Question", "idea": "Idea", "other": "Feedback"}


def _subject_snippet(message: str, limit: int = 60) -> str:
    first_line = " ".join(message.split())
    return first_line if len(first_line) <= limit else first_line[: limit - 1].rstrip() + "…"


def send_feedback_notification_email(
    to_emails: list[str],
    *,
    from_user_email: str,
    from_user_name: str | None,
    kind: str,
    message: str,
    page_url: str | None,
) -> None:
    """Tell admins a user sent feedback or a support question — see
    app.services.feedback.create_feedback, the only caller.

    Deliberately a plain, conversational email rather than one of the branded
    templates above: it reads like a message from the person (their name in
    From, their words first), and Reply-To is their address, so answering it
    is just replying in your mail client. The app context goes in a short
    signature-style footer. Same log-only local-dev fallback as
    send_magic_link_email.
    """
    sender = from_user_name or from_user_email
    label = _FEEDBACK_KIND_LABELS.get(kind, "Feedback")
    subject = f"{label} from {sender}: {_subject_snippet(message)}"
    context = [f"{label} sent from Yabot Jobs by {sender} <{from_user_email}>"]
    if page_url:
        context.append(f"Page: {settings.frontend_base_url}{page_url}")
    context.append(f"All feedback: {settings.frontend_base_url}/admin/feedback")
    body = f"{message}\n\n-- \n" + "\n".join(context) + "\n"
    # Display name only; the address stays ours (SES only sends from
    # verified identities). Quotes/angle brackets would break the header.
    display_name = sender.replace('"', "").replace("<", "").replace(">", "")
    from_address = f'"{display_name} via Yabot Jobs" <{settings.email_from_address}>'

    if settings.email_sender_access_key_id is None:
        logger.info(
            "Feedback notification (not sent: no SES credentials)\nTo: %s\nFrom: %s\nReply-To: %s\nSubject: %s\n\n%s",
            ", ".join(to_emails),
            from_address,
            from_user_email,
            subject,
            body,
        )
        return

    try:
        _get_client().send_email(
            FromEmailAddress=from_address,
            Destination={"ToAddresses": to_emails},
            ReplyToAddresses=[from_user_email],
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
        )
    except ClientError:
        logger.exception("Failed to send feedback notification email to %s", to_emails)
        raise
