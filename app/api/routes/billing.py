import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.user import User
from app.schemas.billing import BillingRedirectRead
from app.services import billing
from app.services.ai_access import subscriptions_enabled

logger = logging.getLogger("app.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


def _stripe_failed(exc: stripe.StripeError) -> HTTPException:
    logger.exception("Stripe request failed")
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Couldn't reach our payment provider. Try again in a moment.",
    )


@router.post("/checkout", response_model=BillingRedirectRead)
def start_checkout(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BillingRedirectRead:
    if not subscriptions_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Subscriptions aren't available.")
    try:
        return BillingRedirectRead(url=billing.create_checkout_session(db, current_user))
    except billing.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise _stripe_failed(exc) from exc


@router.post("/portal", response_model=BillingRedirectRead)
def open_portal(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BillingRedirectRead:
    # Only needs the secret key: a subscriber can still manage or cancel a
    # plan even if new sign-ups have been switched off.
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing isn't available.")
    try:
        return BillingRedirectRead(url=billing.create_portal_session(db, current_user))
    except billing.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise _stripe_failed(exc) from exc


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    """Stripe → app subscription updates. Unauthenticated by design; the
    Stripe-Signature header is what proves the request came from Stripe."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook not configured.")
    payload = await request.body()
    try:
        event = billing.construct_event(payload, request.headers.get("stripe-signature"))
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook.") from exc
    # Any failure here (e.g. Stripe unreachable while re-fetching the
    # subscription) is a 500, which makes Stripe retry the delivery later.
    # handle_event makes a blocking Stripe call — keep it off the event loop.
    await run_in_threadpool(billing.handle_event, db, event)
    return {"received": True}
