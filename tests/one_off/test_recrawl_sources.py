from types import SimpleNamespace

import pytest

from one_off import recrawl_sources


def _source(n: int) -> SimpleNamespace:
    return SimpleNamespace(id=f"source-{n}", name=f"Source {n}", ats_type="ashby", board_url=f"https://x/{n}")


@pytest.mark.parametrize(
    "count,size,expected_sizes",
    [
        (0, 8, []),
        (8, 8, [8]),  # exact multiple — one batch, no remainder
        (10, 8, [8, 2]),  # remainder becomes a smaller final batch
        (3, 8, [3]),  # fewer items than one batch
    ],
)
def test_batched_splits_into_expected_chunk_sizes(count, size, expected_sizes):
    items = [_source(i) for i in range(count)]
    batches = list(recrawl_sources._batched(items, size))
    assert [len(b) for b in batches] == expected_sizes
    assert [s for batch in batches for s in batch] == items  # nothing dropped or reordered


def test_recrawl_in_batches_enqueues_every_source_in_order(monkeypatch):
    enqueued = []
    monkeypatch.setattr(recrawl_sources, "enqueue_crawl", lambda source_id: enqueued.append(source_id))
    monkeypatch.setattr(recrawl_sources.time, "sleep", lambda _seconds: None)
    sources = [_source(i) for i in range(10)]

    recrawl_sources._recrawl_in_batches(sources, batch_size=4, batch_delay_seconds=15)

    assert enqueued == [s.id for s in sources]


def test_recrawl_in_batches_sleeps_between_batches_but_not_after_the_last(monkeypatch):
    sleeps = []
    monkeypatch.setattr(recrawl_sources, "enqueue_crawl", lambda source_id: None)
    monkeypatch.setattr(recrawl_sources.time, "sleep", lambda seconds: sleeps.append(seconds))
    sources = [_source(i) for i in range(17)]  # 3 batches of 8, 8, 1

    recrawl_sources._recrawl_in_batches(sources, batch_size=8, batch_delay_seconds=15)

    assert sleeps == [15, 15]  # paused between batch 1->2 and 2->3, not after the final batch


def test_recrawl_in_batches_with_one_batch_never_sleeps(monkeypatch):
    sleeps = []
    monkeypatch.setattr(recrawl_sources, "enqueue_crawl", lambda source_id: None)
    monkeypatch.setattr(recrawl_sources.time, "sleep", lambda seconds: sleeps.append(seconds))
    sources = [_source(i) for i in range(5)]

    recrawl_sources._recrawl_in_batches(sources, batch_size=8, batch_delay_seconds=15)

    assert sleeps == []


def test_recrawl_in_batches_continues_past_a_single_enqueue_failure(monkeypatch):
    enqueued = []

    def fake_enqueue(source_id):
        if source_id == "source-1":
            raise RuntimeError("pubsub down")
        enqueued.append(source_id)

    monkeypatch.setattr(recrawl_sources, "enqueue_crawl", fake_enqueue)
    monkeypatch.setattr(recrawl_sources.time, "sleep", lambda _seconds: None)
    sources = [_source(i) for i in range(3)]

    recrawl_sources._recrawl_in_batches(sources, batch_size=8, batch_delay_seconds=15)  # must not raise

    assert enqueued == ["source-0", "source-2"]  # source-1 skipped, the rest still ran


def test_default_batch_size_matches_crawl_worker_max_instances():
    # See deploy/gcloud-deploy.sh / `gcloud functions describe crawl-worker` —
    # keep this in sync if that capacity ever changes.
    assert recrawl_sources.DEFAULT_BATCH_SIZE == 8
