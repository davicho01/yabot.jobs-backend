"""Stripe billing for the paid plan: a monthly subscription that lets a user
run every resume feature on the system LLM key instead of their own (see
app.services.ai_access for how it's enforced).

Stripe is the source of truth. The app only ever:
- sends the user to Stripe Checkout to subscribe, and to the Stripe
  customer portal to update their card or cancel, and
- mirrors the subscription's state onto the User row from webhooks, so
  access checks are a column read rather than a Stripe API call.

Every subscription webhook re-fetches the subscription from Stripe rather
than trusting the event payload: Stripe doesn't guarantee event order, and
the freshly fetched object is always the latest state regardless of which
event arrived last.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.services.ai_access import ACTIVE_SUBSCRIPTION_STATUSES, has_active_subscription

logger = logging.getLogger("app.billing")

SUBSCRIPTION_EVENTS = frozenset(
    {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}
)


# A subscription that still exists but whose payment failed. The fix is a
# new card in the customer portal; a second Checkout would start a second,
# parallel subscription.
PAYMENT_ISSUE_STATUSES = frozenset({"past_due", "unpaid", "incomplete"})


class BillingError(Exception):
    """A user-facing billing problem (routes surface the message as a 4xx)."""


def _as_dict(obj: Any) -> dict:
    """Stripe's SDK objects aren't dicts; everything below works on plain
    (recursively converted) dicts so it can also be fed test fixtures."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(settings.stripe_secret_key)


def _return_url(query: str = "") -> str:
    # The AI API Keys page is where the plan is bought and managed.
    return f"{settings.frontend_base_url}/api-keys{query}"


def _ensure_customer(db: Session, user: User, client: stripe.StripeClient) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = client.v1.customers.create(
        params={"email": user.email, "metadata": {"user_id": str(user.id)}},
        # Two clicks on Subscribe must not make two Stripe customers.
        options={"idempotency_key": f"customer-{user.id}"},
    )
    user.stripe_customer_id = customer.id
    db.flush()
    return customer.id


def create_checkout_session(db: Session, user: User) -> str:
    """Start a Stripe Checkout for the plan and return its URL."""
    if has_active_subscription(user):
        raise BillingError("You're already subscribed. Manage your plan from AI API Keys.")
    if user.subscription_status in PAYMENT_ISSUE_STATUSES:
        raise BillingError("Your last payment didn't go through. Update your card from Manage billing instead.")
    client = _client()
    customer_id = _ensure_customer(db, user, client)
    session = client.v1.checkout.sessions.create(
        params={
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": settings.stripe_price_id, "quantity": 1}],
            # How the checkout.session.completed webhook finds the user.
            "client_reference_id": str(user.id),
            "subscription_data": {"metadata": {"user_id": str(user.id)}},
            "success_url": _return_url("?checkout=success"),
            "cancel_url": _return_url("?checkout=canceled"),
        }
    )
    return session.url


def create_portal_session(db: Session, user: User) -> str:
    """A Stripe customer-portal link, where the user updates their card,
    sees invoices, or cancels."""
    if not user.stripe_customer_id:
        raise BillingError("You don't have a subscription to manage yet.")
    session = _client().v1.billing_portal.sessions.create(
        params={"customer": user.stripe_customer_id, "return_url": _return_url()}
    )
    return session.url


def _period_end(subscription: dict) -> datetime | None:
    """The current period's end. Newer Stripe API versions report it per
    subscription item rather than on the subscription itself; a plan with
    one price has one item, but take the latest in case that changes."""
    ends = [item.get("current_period_end") for item in (subscription.get("items") or {}).get("data", [])]
    ends = [end for end in ends if end]
    if not ends and subscription.get("current_period_end"):
        ends = [subscription["current_period_end"]]
    return datetime.fromtimestamp(max(ends), tz=timezone.utc) if ends else None


def sync_subscription(db: Session, subscription: dict) -> User | None:
    """Copy a Stripe subscription's state onto its user. Returns the user, or
    None if no user has that Stripe customer."""
    customer_id = subscription.get("customer")
    if not isinstance(customer_id, str):
        customer_id = customer_id.get("id") if customer_id else None
    user = db.scalar(select(User).where(User.stripe_customer_id == customer_id)) if customer_id else None
    if user is None:
        logger.warning(
            "Stripe subscription %s has no matching user (customer %s)", subscription.get("id"), customer_id
        )
        return None

    # A user should only ever have one live subscription, but if an old one
    # sends a late cancellation after they've re-subscribed, it mustn't
    # overwrite the one that's actually active.
    incoming_active = subscription.get("status") in ACTIVE_SUBSCRIPTION_STATUSES
    if (
        user.stripe_subscription_id
        and user.stripe_subscription_id != subscription.get("id")
        and user.subscription_status in ACTIVE_SUBSCRIPTION_STATUSES
        and not incoming_active
    ):
        logger.info("Ignoring stale subscription %s for user %s", subscription.get("id"), user.id)
        return user

    user.stripe_subscription_id = subscription.get("id")
    user.subscription_status = subscription.get("status")
    user.subscription_current_period_end = _period_end(subscription)
    user.subscription_cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
    db.flush()
    return user


def _link_checkout_customer(db: Session, session: dict) -> None:
    """Checkout normally reuses the customer made in create_checkout_session,
    but make sure the user row points at whichever customer actually paid."""
    user_id = session.get("client_reference_id")
    customer_id = session.get("customer")
    if not user_id or not isinstance(customer_id, str):
        return
    try:
        user = db.get(User, uuid.UUID(user_id))
    except ValueError:
        return
    if user is not None and user.stripe_customer_id != customer_id:
        user.stripe_customer_id = customer_id
        db.flush()


def handle_event(db: Session, event: Any) -> None:
    """Apply one verified Stripe webhook event. Idempotent — Stripe retries
    deliveries, and replaying any of these just re-syncs the same state."""
    event = _as_dict(event)
    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return
        _link_checkout_customer(db, obj)
        subscription_id = obj.get("subscription")
    elif event_type in SUBSCRIPTION_EVENTS:
        subscription_id = obj.get("id")
    else:
        return

    if not subscription_id:
        return
    subscription = _client().v1.subscriptions.retrieve(subscription_id)
    sync_subscription(db, _as_dict(subscription))


def construct_event(payload: bytes, signature: str | None) -> Any:
    """Verify a webhook's signature and parse it. Raises ValueError /
    stripe.SignatureVerificationError on a bad payload or signature."""
    return stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
