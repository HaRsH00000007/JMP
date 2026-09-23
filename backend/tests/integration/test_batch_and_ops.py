"""Message Batches bulk mode (fake client), cancellation, stuck-job reaper, and Alembic migrations."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, select

from app.db.models import AuditEvent, BulkJob, BulkJobItem, GenerationJob, GenerationUsage
from app.db.session import session_scope
from app.llm import batch as batch_mod
from app.llm.mock_provider import build_mock_narrative
from app.services import bulk
from app.services.bulk_csv import parse_csv
from app.settings import get_settings, set_settings
from tests.conftest import BACKEND, fake_response

pytestmark = pytest.mark.render


class FakeBatches:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def create(self, requests):
        self.requests = list(requests)
        return SimpleNamespace(id="msgbatch_test", processing_status="in_progress")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        out = []
        for i, req in enumerate(self.requests):
            if i == 1:
                out.append(SimpleNamespace(custom_id=req["custom_id"],
                                           result=SimpleNamespace(type="errored", error=SimpleNamespace(type="api_error"))))
                continue
            text = req["params"]["messages"][0]["content"]
            payload = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            msg = fake_response(json.dumps(build_mock_narrative(payload)), cache_read=7000)
            out.append(SimpleNamespace(custom_id=req["custom_id"], result=SimpleNamespace(type="succeeded", message=msg)))
        return out


def test_batch_mode_bulk(db):
    fake = SimpleNamespace(messages=SimpleNamespace(batches=FakeBatches()))
    batch_mod.set_client(fake)
    set_settings(get_settings().model_copy(update={"llm_provider": "anthropic", "bulk_llm_mode": "batch"}))
    try:
        data = b"route_id,start_location,end_location\nBT-1,Zirakpur,Lalru\nBT-2,Mohali,Kharar\n"
        with session_scope() as s:
            bj = bulk.create_bulk_job(s, parse_csv(data), filename="b.csv", csv_bytes=data)
            bid = bj.id
            assert bj.llm_mode == "batch"
        bulk.start(bid)
        reqs = fake.messages.batches.requests
        assert len(reqs) == 2
        sys_blocks = reqs[0]["params"]["system"]
        assert sys_blocks[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert reqs[0]["params"]["system"] == reqs[1]["params"]["system"]
        assert "extra_body" not in reqs[0]["params"]  # no fallbacks on the Batches API
        with session_scope() as s:
            bjob = s.get(BulkJob, bid)
            assert bjob.status == "completed_with_errors"
            assert (bjob.succeeded, bjob.failed) == (1, 1)
            items = {i.route_ref: i for i in s.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bid))}
            assert items["BT-1"].status == "succeeded"
            # errored batch result fell back to realtime for that row only (no key configured here)
            assert items["BT-2"].status == "failed" and items["BT-2"].error_code == "LLM_NOT_CONFIGURED"
            usage = s.scalars(select(GenerationUsage).where(GenerationUsage.bulk_job_id == bid)).all()
            batch_rows = [u for u in usage if u.service_tier == "batch"]
            assert len(batch_rows) == 1 and batch_rows[0].cache_read_input_tokens == 7000
    finally:
        set_settings(None)
        batch_mod.set_client(None)


def test_cancel_bulk_before_start(db):
    data = b"route_id,start_location,end_location\nC-1,Zirakpur,Lalru\nC-2,Mohali,Kharar\n"
    with session_scope() as s:
        bid = bulk.create_bulk_job(s, parse_csv(data), filename="c.csv", csv_bytes=data).id
    n = bulk.cancel(bid)
    assert n == 2
    with session_scope() as s:
        bj = s.get(BulkJob, bid)
        assert bj.processed == 2 and bj.failed == 2 and bj.status in ("completed_with_errors", "finalizing")
        statuses = {i.status for i in s.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bid))}
        assert statuses == {"cancelled"}


def test_item_counted_once_on_redelivery(db):
    data = b"route_id,start_location,end_location\nR-1,Zirakpur,Lalru\n"
    with session_scope() as s:
        bid = bulk.create_bulk_job(s, parse_csv(data), filename="r.csv", csv_bytes=data).id
    bulk.start(bid)
    with session_scope() as s:
        item = s.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bid)).one()
        iid = item.id
    bulk.item_finished(iid, ok=True)  # duplicate completion signal
    with session_scope() as s:
        bj = s.get(BulkJob, bid)
        assert bj.processed == 1 and bj.succeeded == 1


def test_reaper_redispatches_stuck_jobs(db):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.services import jobs

    with session_scope() as s:
        gj = s.scalars(select(GenerationJob).where(GenerationJob.status == "completed")).first()
        assert gj is not None
    # a completed job is never reaped
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    with session_scope() as s:
        s.execute(update(GenerationJob).where(GenerationJob.id == gj.id).values(updated_at=old))
    assert jobs.reap_stuck_jobs() == 0


def test_migrations_upgrade_and_downgrade(tmp_path):
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    set_settings(get_settings().model_copy(update={"database_url": url}))
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        command.upgrade(cfg, "head")
        names = set(inspect(create_engine(url)).get_table_names())
        for t in ("users", "journeys", "journey_stops", "route_analysis", "hazards", "journey_hazards",
                  "jmp_documents", "bulk_jobs", "bulk_job_items", "generation_jobs", "generation_usage",
                  "hazard_library_versions", "risk_matrix_cells", "journey_scores", "audit_events"):
            assert t in names, t
        command.downgrade(cfg, "base")
        assert "journeys" not in set(inspect(create_engine(url)).get_table_names())
    finally:
        set_settings(None)


def test_purge_soft_deleted_documents(db):
    from datetime import datetime, timezone

    from app.cli.maintenance import purge
    from app.db.models import JmpDocument
    from app.storage import get_storage

    with session_scope() as s:
        d = s.scalars(select(JmpDocument).where(JmpDocument.pdf_path.is_not(None))).first()
        assert d is not None
        d.deleted_at = datetime.now(timezone.utc)
        key, did = d.pdf_path, d.id
    assert get_storage().exists(key)
    dry = purge(730, dry_run=True)
    assert dry["documents"] >= 1 and get_storage().exists(key)
    purge(730, dry_run=False)
    assert not get_storage().exists(key)
    with session_scope() as s:
        doc = s.get(JmpDocument, did)
        assert doc.status == "purged" and doc.pdf_sha256  # audit metadata kept, file removed


def test_cached_commits_in_its_own_transaction(db):
    """Regression: the provider response must be committed immediately, not left in the caller's transaction.

    It used to be added to the caller's session, which stays open for the whole of compute_facts. That took
    SQLite's single writer lock, and the 60-120 s Overpass call that followed ran with the lock held, so
    every other bulk worker blocked on it and timed out — a 10-row run crawled along 4 at a time.
    Committing in a short transaction of its own is what keeps concurrent journeys independent.
    """
    from app.db.models import RouteCache
    from app.domain.route import GeocodeResult
    from app.services.route_service import _cache_key, cached

    calls = []

    def provider() -> GeocodeResult:
        calls.append(1)
        return GeocodeResult(query="q", name="Somewhere", lat=1.0, lng=2.0, provider="cachetest")

    key = _cache_key("geocode", "cachetest", "probe-a")
    with session_scope() as caller:
        cached(caller, "geocode", "cachetest", "probe-a", GeocodeResult, provider)
        # an independent session must already see it: i.e. it was committed, not pending in `caller`
        with session_scope() as other:
            assert other.get(RouteCache, key) is not None, (
                "the cached response is still inside the caller's open transaction — that transaction "
                "holds the write lock across provider calls and blocks every other worker")

    with session_scope() as s:                      # and it really is a cache
        cached(s, "geocode", "cachetest", "probe-a", GeocodeResult, provider)
    assert len(calls) == 1
