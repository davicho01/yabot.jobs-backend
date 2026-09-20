import logging


def configure_logging() -> None:
    """Make INFO-level application logs actually appear, locally and in prod.

    logging.basicConfig(level=INFO) alone isn't enough on the Cloud Functions
    runtime: it configures logging before importing our module, so the root
    logger already has a handler and basicConfig silently does nothing —
    leaving the root level at WARNING, which is why only warnings and
    tracebacks ever showed up in Cloud Logging (every "Processing…" /
    "Finished…" / "Lane…" line was dropped). Setting the level on the root
    logger explicitly works whether or not something configured it first.
    """
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    # httpx logs the full request URL at INFO level on every call — noisy
    # given how many fetches one scan makes (job page, ATS APIs,
    # browser_fetch_service).
    logging.getLogger("httpx").setLevel(logging.WARNING)
