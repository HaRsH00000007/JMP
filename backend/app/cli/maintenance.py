"""Operational maintenance (D-19 retention, stuck jobs).

    python -m app.cli.maintenance purge [--retention-days 730] [--dry-run]
        * removes files of soft-deleted documents (DELETE /documents/{id}) and marks them purged
        * removes documents older than the retention period (default 2 years, D-19) — rows keep their
          audit metadata (versions, hashes, usage); only the stored HTML/PDF files are deleted
        * removes validated-but-unused CSV uploads older than 24 h
    python -m app.cli.maintenance reap
        * re-dispatches jobs stuck in 'processing' past STUCK_JOB_TIMEOUT_MIN (also scheduled by Celery beat)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.db.models import AuditEvent, JmpDocument, UploadedCsv
from app.db.session import session_scope
from app.services import jobs
from app.storage import get_storage


def purge(retention_days: int, dry_run: bool) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    storage = get_storage()
    counts = {"documents": 0, "files": 0, "uploads": 0}
    with session_scope() as db:
        docs = db.scalars(select(JmpDocument).where(
            or_(JmpDocument.deleted_at.is_not(None), JmpDocument.created_at < cutoff),
            JmpDocument.pdf_path.is_not(None))).all()
        for d in docs:
            counts["documents"] += 1
            for key in (d.pdf_path, d.html_path):
                if key and storage.exists(key):
                    counts["files"] += 1
                    if not dry_run:
                        storage.delete(key)
            if not dry_run:
                d.pdf_path = d.html_path = None
                d.status = "purged"
                d.deleted_at = d.deleted_at or now
                db.add(AuditEvent(entity="jmp_document", entity_id=str(d.id), action="purge",
                                  detail={"retention_days": retention_days}))
        for up in db.scalars(select(UploadedCsv).where(UploadedCsv.created_at < now - timedelta(hours=24))):
            counts["uploads"] += 1
            if not dry_run:
                if storage.exists(up.csv_path):
                    storage.delete(up.csv_path)
                db.delete(up)
        if dry_run:
            db.rollback()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("purge")
    p.add_argument("--retention-days", type=int, default=730)
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("reap")
    args = ap.parse_args(argv)
    if args.cmd == "purge":
        print(json.dumps({"dry_run": args.dry_run, **purge(args.retention_days, args.dry_run)}))
    else:
        print(json.dumps({"requeued": jobs.reap_stuck_jobs()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
