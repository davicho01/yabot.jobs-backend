import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models as m
import main
from app.api.deps import get_db
from app.api.routes.billing import open_portal, start_checkout
from app.api.routes.onboarding import read_ai_access
from app.core.config import settings
from app.db.base import Base
from app.services import ai_access, billing

WEBHOOK_SECRET = "whsec_test"
PERIOD_END = 1_900_000_000  # 2030-03-17


@pytest.fixture(autouse=True)
def real_encryption_key(monkeypatch):
    # These tests store real UserApiKey rows, which encrypts them. CI's
    # API_KEY_ENCRYPTION_KEY is a deliberate non-key placeholder, so give
    # each test a throwaway valid one instead of depending on the local .env.
    monkeypatch.setattr(settings, "api_key_encryption_key", Fernet.generate_key().decode())


@pytest.fixture
def engine():
    # StaticPool + check_same_thread=False: the webhook route hands the
    # session to a worker thread (run_in_threadpool).
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(
        engine,
        tables=[
            m.User.__table__,
            m.UserApiKey.__table__,
            m.FreeTrialJob.__table__,
            m.SubscriptionEvaluationJob.__table__,
        ],
    )
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_user_api_keys_one_default_per_user"))
    return engine


@pytest.fixture
def db(engine) -> Session:
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def stripe_on(monkeypatch):
    monkeypatch.setattr(settings, "system_llm_provider", "anthropic")
    monkeypatch.setattr(settings, "system_llm_model", "system-model")
    monkeypatch.setattr(settings, "system_llm_api_key", "system-key")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(settings, "stripe_price_id", "price_plan")
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "subscription_monthly_request_limit", 3)
    monkeypatch.setattr(settings, "frontend_base_url", "https://app.example")


class FakeStripe:
    """Records calls and returns canned objects, standing in for
    stripe.StripeClient's v1 services."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.subscriptions: dict[str, dict] = {}

        def recorder(name, result):
            def call(*args, **kwargs):
                self.calls.append((name, kwargs.get("params") or (args[0] if args else None)))
                return result(*args, **kwargs) if callable(result) else result

            return call

        self.v1 = SimpleNamespace(
            customers=SimpleNamespace(create=recorder("customers.create", SimpleNamespace(id="cus_new"))),
            checkout=SimpleNamespace(
                sessions=SimpleNamespace(
                    create=recorder("checkout.create", SimpleNamespace(url="https://checkout.stripe/xyz"))
                )
            ),
            billing_portal=SimpleNamespace(
                sessions=SimpleNamespace(
                    create=recorder("portal.create", SimpleNamespace(url="https://billing.stripe/abc"))
                )
            ),
            subscriptions=SimpleNamespace(
                retrieve=recorder("subscriptions.retrieve", lambda sub_id, *a, **k: self.subscriptions[sub_id])
            ),
        )

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def fake_stripe(monkeypatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(billing, "_client", lambda: fake)
    return fake


def _make_user(db, **fields) -> m.User:
    user = m.User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", **fields)
    db.add(user)
    db.commit()
    return user


def _subscription(sub_id="sub_1", customer="cus_1", status="active", period_end=PERIOD_END, cancel=False) -> dict:
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": customer,
        "status": status,
        "cancel_at_period_end": cancel,
        "items": {"object": "list", "data": [{"id": "si_1", "current_period_end": period_end}]},
    }


def _subscribed_user(db, **fields) -> m.User:
    defaults = {
        "stripe_customer_id": "cus_1",
        "stripe_subscription_id": "sub_1",
        "subscription_status": "active",
        "subscription_current_period_end": datetime.fromtimestamp(PERIOD_END, tz=timezone.utc),
    }
    return _make_user(db, **{**defaults, **fields})


# ------------------------------------------------------------------ settings


def test_subscriptions_need_stripe_and_a_system_key(monkeypatch):
    assert ai_access.subscriptions_enabled() is True
    monkeypatch.setattr(settings, "stripe_price_id", None)
    assert ai_access.subscriptions_enabled() is False
    monkeypatch.setattr(settings, "stripe_price_id", "price_plan")
    monkeypatch.setattr(settings, "system_llm_api_key", None)
    assert ai_access.subscriptions_enabled() is False


# ------------------------------------------------------------------ checkout / portal


def test_checkout_creates_the_customer_once_and_returns_the_url(db, fake_stripe):
    user = _make_user(db)

    first = start_checkout(current_user=user, db=db)
    second = start_checkout(current_user=user, db=db)

    assert first.url == second.url == "https://checkout.stripe/xyz"
    assert user.stripe_customer_id == "cus_new"
    assert fake_stripe.names().count("customers.create") == 1
    params = dict(fake_stripe.calls)["checkout.create"]
    assert params["mode"] == "subscription"
    assert params["customer"] == "cus_new"
    assert params["line_items"] == [{"price": "price_plan", "quantity": 1}]
    assert params["client_reference_id"] == str(user.id)
    assert params["success_url"] == "https://app.example/api-keys?checkout=success"


def test_checkout_refuses_an_already_active_subscriber(db, fake_stripe):
    user = _subscribed_user(db)

    with pytest.raises(HTTPException) as exc_info:
        start_checkout(current_user=user, db=db)
    assert exc_info.value.status_code == 409


def test_checkout_refuses_a_subscription_with_a_failed_payment(db, fake_stripe):
    user = _subscribed_user(db, subscription_status="past_due")

    with pytest.raises(HTTPException) as exc_info:
        start_checkout(current_user=user, db=db)
    assert exc_info.value.status_code == 409
    assert "Update your card" in exc_info.value.detail


def test_checkout_503s_when_subscriptions_are_off(db, fake_stripe, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    with pytest.raises(HTTPException) as exc_info:
        start_checkout(current_user=_make_user(db), db=db)
    assert exc_info.value.status_code == 503


def test_portal_needs_a_customer(db, fake_stripe):
    with pytest.raises(HTTPException) as exc_info:
        open_portal(current_user=_make_user(db), db=db)
    assert exc_info.value.status_code == 409

    result = open_portal(current_user=_subscribed_user(db), db=db)
    assert result.url == "https://billing.stripe/abc"
    assert dict(fake_stripe.calls)["portal.create"] == {"customer": "cus_1", "return_url": "https://app.example/api-keys"}


# ------------------------------------------------------------------ webhook sync


def test_checkout_completed_activates_the_subscription(db, fake_stripe):
    user = _make_user(db, stripe_customer_id="cus_1")
    fake_stripe.subscriptions["sub_1"] = _subscription()

    billing.handle_event(
        db,
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "mode": "subscription",
                    "client_reference_id": str(user.id),
                    "customer": "cus_1",
                    "subscription": "sub_1",
                }
            },
        },
    )

    assert user.subscription_status == "active"
    assert user.stripe_subscription_id == "sub_1"
    assert user.subscription_current_period_end == datetime.fromtimestamp(PERIOD_END, tz=timezone.utc)
    assert ai_access.has_active_subscription(user) is True


def test_subscription_events_refetch_the_latest_state(db, fake_stripe):
    user = _subscribed_user(db)
    # The event payload is stale ("active"); Stripe's current state is canceled.
    fake_stripe.subscriptions["sub_1"] = _subscription(status="canceled")

    billing.handle_event(
        db, {"type": "customer.subscription.updated", "data": {"object": _subscription(status="active")}}
    )

    assert user.subscription_status == "canceled"
    assert ai_access.has_active_subscription(user) is False


def test_cancel_at_period_end_keeps_access_until_then(db, fake_stripe):
    user = _subscribed_user(db)
    fake_stripe.subscriptions["sub_1"] = _subscription(cancel=True)

    billing.handle_event(db, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})

    assert user.subscription_cancel_at_period_end is True
    assert ai_access.has_active_subscription(user) is True


def test_a_scheduled_cancel_at_counts_as_not_renewing(db, fake_stripe):
    # What the customer portal sends on newer API versions: cancel_at set,
    # cancel_at_period_end left false.
    user = _subscribed_user(db)
    fake_stripe.subscriptions["sub_1"] = {**_subscription(), "cancel_at": PERIOD_END}

    billing.handle_event(db, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})

    assert user.subscription_cancel_at_period_end is True
    assert ai_access.has_active_subscription(user) is True


def test_a_late_cancellation_of_an_old_subscription_is_ignored(db, fake_stripe):
    user = _subscribed_user(db)
    user.stripe_subscription_id = "sub_new"
    db.commit()
    fake_stripe.subscriptions["sub_old"] = _subscription(sub_id="sub_old", status="canceled")

    billing.handle_event(db, {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_old"}}})

    assert user.stripe_subscription_id == "sub_new"
    assert user.subscription_status == "active"


def test_unknown_customer_and_unrelated_events_are_ignored(db, fake_stripe):
    fake_stripe.subscriptions["sub_x"] = _subscription(sub_id="sub_x", customer="cus_nobody")
    billing.handle_event(db, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_x"}}})
    billing.handle_event(db, {"type": "invoice.paid", "data": {"object": {"id": "in_1"}}})
    billing.handle_event(
        db, {"type": "checkout.session.completed", "data": {"object": {"mode": "payment", "subscription": None}}}
    )
    assert fake_stripe.names() == ["subscriptions.retrieve"]


def _signed(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


@pytest.fixture
def client(engine):
    def override_get_db():
        session = sessionmaker(bind=engine, autoflush=False)()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app)
    main.app.dependency_overrides.pop(get_db, None)


def test_webhook_route_verifies_the_signature(db, client, fake_stripe):
    user = _make_user(db, stripe_customer_id="cus_1")
    fake_stripe.subscriptions["sub_1"] = _subscription()
    payload = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": "customer.subscription.created",
            "data": {"object": _subscription()},
        }
    ).encode()

    bad = client.post("/billing/webhook", content=payload, headers={"stripe-signature": _signed(payload, "whsec_wrong")})
    assert bad.status_code == 400
    missing = client.post("/billing/webhook", content=payload)
    assert missing.status_code == 400

    good = client.post("/billing/webhook", content=payload, headers={"stripe-signature": _signed(payload)})
    assert good.status_code == 200
    db.expire_all()
    assert db.get(m.User, user.id).subscription_status == "active"


# ------------------------------------------------------------------ access + metering


def test_subscriber_requests_run_on_the_system_key_and_are_counted(db):
    user = _subscribed_user(db)

    credentials = ai_access.resolve_llm_credentials(db, user)

    assert credentials.api_key == "system-key"
    assert credentials.source == "subscription"
    assert user.subscription_usage_count == 1
    assert ai_access.subscription_requests_used(user) == 1


def test_subscriber_monthly_cap_and_reset(db):
    user = _subscribed_user(db)
    for _ in range(3):
        ai_access.resolve_llm_credentials(db, user)

    with pytest.raises(HTTPException) as exc_info:
        ai_access.resolve_llm_credentials(db, user)
    assert exc_info.value.status_code == 429
    assert "They reset on March 17" in exc_info.value.detail

    # Stripe renews the plan: a new period, so the count starts over.
    user.subscription_current_period_end += timedelta(days=30)
    db.commit()
    assert ai_access.subscription_requests_used(user) == 0
    ai_access.resolve_llm_credentials(db, user)
    assert user.subscription_usage_count == 1


def test_zero_limit_means_unlimited(db, monkeypatch):
    monkeypatch.setattr(settings, "subscription_monthly_request_limit", 0)
    user = _subscribed_user(db)
    for _ in range(10):
        ai_access.resolve_llm_credentials(db, user)
    assert user.subscription_usage_count == 10


def _add_own_key(db, user: m.User) -> m.UserApiKey:
    key = m.UserApiKey(user_id=user.id, provider="openai", label="default", model="own", is_default=True)
    key.set_plaintext_key("own-key")
    db.add(key)
    db.commit()
    return key


def test_subscription_beats_a_saved_key(db):
    # Someone paying for the plan must never also be billed by their own
    # provider without realizing it.
    user = _subscribed_user(db)
    _add_own_key(db, user)

    assert ai_access.resolve_llm_credentials(db, user).source == "subscription"
    assert ai_access.job_llm_credentials(db, user, uuid.uuid4()).source == "subscription"
    # Resume-wide features meter by request; job features meter by distinct
    # job unlocked — separate counters.
    assert user.subscription_usage_count == 1
    assert user.subscription_evaluations_used == 1


def test_saved_key_takes_over_when_the_plan_ends(db):
    user = _subscribed_user(db, subscription_status="canceled")
    _add_own_key(db, user)

    credentials = ai_access.resolve_llm_credentials(db, user)

    assert credentials.api_key == "own-key"
    assert user.subscription_usage_count == 0


def test_ai_access_reports_what_requests_run_on(db, monkeypatch):
    monkeypatch.setattr(settings, "free_evaluation_limit", 5)
    user = _subscribed_user(db)
    _add_own_key(db, user)

    summary = read_ai_access(current_user=user, db=db)
    assert summary.active_source == "subscription"
    assert summary.has_own_key is True
    assert (summary.own_key_provider, summary.own_key_model) == ("openai", "own")

    user.subscription_status = "canceled"
    assert read_ai_access(current_user=user, db=db).active_source == "own_key"

    other = _make_user(db)
    assert read_ai_access(current_user=other, db=db).active_source == "free_trial"
    other.free_evaluations_used = 5
    assert read_ai_access(current_user=other, db=db).active_source is None


def test_subscriber_scores_dont_spend_free_evaluations(db, monkeypatch):
    monkeypatch.setattr(settings, "free_evaluation_limit", 5)
    user = _subscribed_user(db)

    credentials = ai_access.job_llm_credentials(db, user, uuid.uuid4())

    assert credentials.source == "subscription"
    assert user.free_evaluations_used == 0


def test_past_due_or_canceled_has_no_access(db):
    user = _subscribed_user(db)
    for status in ("past_due", "canceled", None):
        user.subscription_status = status
        with pytest.raises(HTTPException) as exc_info:
            ai_access.resolve_llm_credentials(db, user)
        assert exc_info.value.status_code == 422
        assert "subscribe for $5/month" in exc_info.value.detail


def test_ai_access_reports_the_subscription(db):
    user = _subscribed_user(db, subscription_cancel_at_period_end=True)
    ai_access.resolve_llm_credentials(db, user)
    ai_access.job_llm_credentials(db, user, uuid.uuid4())

    summary = read_ai_access(current_user=user, db=db)

    assert summary.subscription_available is True
    assert summary.subscribed is True
    assert summary.subscription_price_label == "$5/month"
    assert summary.subscription_cancel_at_period_end is True
    assert summary.subscription_requests_used == 1
    assert summary.subscription_request_limit == 3
    assert summary.subscription_evaluations_used == 1
    assert summary.subscription_evaluation_limit == 100


# ------------------------------------------------------------------ subscription job cap


def test_new_subscriber_defaults_to_100_evaluations(db):
    user = _subscribed_user(db)
    assert user.subscription_evaluation_limit == 100


def test_subscriber_job_cap_and_an_unlocked_job_stays_free(db):
    user = _subscribed_user(db, subscription_evaluation_limit=2)
    job_a, job_b, job_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    ai_access.job_llm_credentials(db, user, job_a)
    ai_access.job_llm_credentials(db, user, job_b)
    assert user.subscription_evaluations_used == 2

    with pytest.raises(HTTPException) as exc_info:
        ai_access.job_llm_credentials(db, user, job_c)
    assert exc_info.value.status_code == 429
    assert "used this month's 2 AI evaluations" in exc_info.value.detail

    # Out of new unlocks, but everything on an already-unlocked job is free.
    for _ in range(3):
        assert ai_access.job_llm_credentials(db, user, job_a).source == "subscription"
    assert user.subscription_evaluations_used == 2


def test_subscriber_job_cap_resets_on_period_rollover(db):
    user = _subscribed_user(db, subscription_evaluation_limit=1)
    job_a, job_b = uuid.uuid4(), uuid.uuid4()
    ai_access.job_llm_credentials(db, user, job_a)

    with pytest.raises(HTTPException):
        ai_access.job_llm_credentials(db, user, job_b)

    # Stripe renews the plan: a new period, so the count — and job_a's
    # unlock — start over.
    user.subscription_current_period_end += timedelta(days=30)
    db.commit()
    assert ai_access.subscription_evaluations_used(user) == 0
    ai_access.job_llm_credentials(db, user, job_a)
    assert user.subscription_evaluations_used == 1


def test_subscriber_job_cap_zero_means_unlimited(db):
    user = _subscribed_user(db, subscription_evaluation_limit=0)
    for _ in range(10):
        ai_access.job_llm_credentials(db, user, uuid.uuid4())
    assert user.subscription_evaluations_used == 10
