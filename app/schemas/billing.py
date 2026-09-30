from pydantic import BaseModel


class BillingRedirectRead(BaseModel):
    """A Stripe-hosted page (Checkout or the customer portal) to send the
    browser to."""

    url: str
