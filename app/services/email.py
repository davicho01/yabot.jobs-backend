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


# Shared by every template below — a 32x32 rounded-square "Y" mark, inlined
# as a data URI since email clients won't reliably fetch an external image.
_YABOT_LOGO_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAACXBIWXMAADsOAAA7DgHMtqGDAAACQElEQVRYhW"
    "NgQAMyMjK6MjLyE2Vk5K/Iysp/kZWV/08h/gIyS0ZGfoKMjIwOAy6goqLCLiMjN01WVv4vFSzFhf/IyspP0dLSYsOwXFZWbh8NLUbBMjLye1E"
    "cISMjN51eliPhyfA4p3Gw44kOWW0GUIIbAMuhUSHXB3LA1YFygKys/GUGWVn5z6RoklIz/S8jp0QlB8h9YiBFA59H63+Gwsf/WZMO/JeRV6a"
    "KIxiIVSitpPWfMfcW2AEgLGoaTl8HCDoUwi0HYR6/SfR1AGviPhQHMKef/S8rq0AfB4gbeKFYDsMSus70cQB34Awkix/B2fxudbRwgMJ/YZ"
    "u0//wu1XCMnPh4ffvgbLb4nYhQ0vdA0YOOQWZiizIGdAEh63SswQ3LftLKOv8Z8u9DxAoe/pdSMfgvpWr0nzHvDk59MAwymyIHCNnngtWwx"
    "6xCiNlm/ed3qyVoOdEOkIVGAZ9XOwpGDkIB5zK4oZzhy/+zpJ+G87mC52HoRddPVjaURcKSWjZYEyVT1uX/MvIqtCkHZNEwS8oxjCDm9e6iT"
    "TaUxYJ5vbsxywUtO/o5QMzIH8VyUMIkxxwGch0gK6/8nyn7GtwBwtYpdHaALKh6bkGqnlXIdYDcJ0ocQUkDRUZG7uPAN8lkZOQnDJQDZGTke"
    "kHNch1oj4XeDvgjJyenBe4bgLpL9Pe9/ER4zwjUTZKRkd9DR8t3Gxsbs6L0D0GOAHWXaBwdf0A+x7AcGYC6S6AeCyiFktpnwIFBZlwGJTh4"
    "nCMBADZXj81ElYv0AAAAAElFTkSuQmCC"
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
# app.services.company_logos); a row without one is just the text cell.
_DIGEST_LOGO_CELL_HTML = """\
          <td width="40" valign="top" style="width:40px;padding:16px 0 14px;border-top:1px solid #e8e8ed;">
            <img src="{src}" width="28" height="28" alt="{alt}" style="display:block;border-radius:6px;border:1px solid #e8e8ed;">
          </td>
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
        return ""
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
    search_label = f'"{html.escape(name)}"' if name else "your search"
    headline = f"{total} new match{plural} for {search_label}"
    subject = f"{total} new job match{plural} on Yabot Jobs" if not name else f"{total} new match{plural} for \"{name}\""

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
