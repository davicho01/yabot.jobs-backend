class RateLimitExceeded(Exception):
    """Raised by a service function when a caller has exceeded a throttle
    (see app.services.auth.enforce_magic_link_rate_limit and
    app.services.jobs._enforce_submission_rate_limit). The message is
    user-facing — routes surface it as an HTTPException(429) detail.
    """
