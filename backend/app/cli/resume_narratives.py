"""Resume bulk rows that failed at the narrative stage (e.g. Anthropic credit exhausted), keeping their saved
route analysis — no geocoding, routing or elevation is repeated. Runs a small sample first and continues with
the rest only if the sample passes the automated cost and quality gate.

    python -m app.cli.resume_narratives --bulk <bulk_job_id> [--sample 3] [--max-cost-per-pdf 0.25]
                                        [--max-calls-per-pdf 1.7] [--sample-only]

Gate (all must hold for the sample): every sampled row produced its PDF; each PDF has the expected page count;
its itinerary matches the submitted stops; calls per PDF and cost per PDF are within the limits.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("JOB_EXECUTION", "sync")  # run each resumed job to completion in this process

from sqlalchemy import select, update  # noqa: E402

from app.cli.cost_report import collect  # noqa: E402
from app.db.models import BulkItemStatus, BulkJob, BulkJobItem, BulkStatus, GenerationJob, JmpDocument, Journey  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.services import jobs  # noqa: E402

RESUMABLE = ("LLM_NOT_CONFIGURED", "LLM_UNAVAILABLE", "LLM_INVALID_OUTPUT")


def pending(bulk_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """(item_id, job_id, route_ref) of rows whose job failed after analysis, at the narrative stage."""
    with session_scope() as db:
        rows = db.execute(
            select(BulkJobItem.id, GenerationJob.id, BulkJobItem.route_ref)
            .join(GenerationJob, GenerationJob.id == BulkJobItem.generation_job_id)
            .where(BulkJobItem.bulk_job_id == bulk_id, BulkJobItem.status == BulkItemStatus.failed,
                   GenerationJob.error_code.in_(RESUMABLE), GenerationJob.report_facts.is_not(None))
            .order_by(BulkJobItem.row_number)).all()
    return [(a, b, c) for a, b, c in rows]


def reopen(bulk_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Un-count a failed row so its completion is counted once, as a success or a failure."""
    with session_scope() as db:
        it = db.get(BulkJobItem, item_id)
        assert it is not None and it.counted
        it.status, it.counted, it.error_code, it.error_message, it.finished_at = (
            BulkItemStatus.processing, False, None, None, None)
        db.execute(update(BulkJob).where(BulkJob.id == bulk_id).values(
            processed=BulkJob.processed - 1, failed=BulkJob.failed - 1, status=BulkStatus.processing,
            finished_at=None))


def run(bulk_id: uuid.UUID, rows: list[tuple[uuid.UUID, uuid.UUID, str]]) -> list[dict[str, Any]]:
    out = []
    for item_id, job_id, ref in rows:
        reopen(bulk_id, item_id)
        t0 = time.monotonic()
        jobs.retry_job(job_id)  # sync: narrate -> render -> item_finished
        with session_scope() as db:
            g = db.get(GenerationJob, job_id)
            doc = db.get(JmpDocument, g.document_id) if g and g.document_id else None
            journey = db.get(Journey, g.journey_id) if g else None
            submitted = [s.raw_text for s in sorted(journey.stops, key=lambda s: s.seq)] if journey else []
            itinerary = [s["input_text"] for s in (doc.report_json["route"].get("itinerary") or [])] if doc else []
            out.append({"route": ref, "job": str(job_id), "status": g.status if g else "?",
                        "error": g.error_code if g else None, "pdf": doc.pdf_path if doc else None,
                        "pages": doc.page_count if doc else None, "itinerary_matches": itinerary == submitted,
                        "seconds": round(time.monotonic() - t0, 1)})
        print(json.dumps(out[-1]), flush=True)
    return out


def gate(results: list[dict[str, Any]], metrics: dict[str, Any], max_cost: float, max_calls: float) -> list[str]:
    problems = []
    for r in results:
        if r["status"] != "completed" or not r["pdf"]:
            problems.append(f"{r['route']}: no PDF ({r['error']})")
        elif r["pages"] != 8:
            problems.append(f"{r['route']}: {r['pages']} pages")
        elif not r["itinerary_matches"]:
            problems.append(f"{r['route']}: itinerary does not match the submitted stops")
    s = metrics["summary"]
    if s["cost_per_successful_pdf_usd"] is None or s["cost_per_successful_pdf_usd"] > max_cost:
        problems.append(f"cost per PDF {s['cost_per_successful_pdf_usd']} > {max_cost}")
    if s["calls_per_successful_pdf"] is None or s["calls_per_successful_pdf"] > max_calls:
        problems.append(f"calls per PDF {s['calls_per_successful_pdf']} > {max_calls}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bulk", required=True)
    ap.add_argument("--sample", type=int, default=3)
    ap.add_argument("--max-cost-per-pdf", type=float, default=0.25)
    ap.add_argument("--max-calls-per-pdf", type=float, default=1.7)
    ap.add_argument("--sample-only", action="store_true")
    args = ap.parse_args(argv)
    bulk_id = uuid.UUID(args.bulk)
    todo = pending(bulk_id)
    print(json.dumps({"resumable_rows": [r for _, _, r in todo]}), flush=True)
    if not todo:
        return 0
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sample = run(bulk_id, todo[:args.sample])
    metrics = collect([], started)
    problems = gate(sample, metrics, args.max_cost_per_pdf, args.max_calls_per_pdf)
    print(json.dumps({"sample_metrics": metrics["summary"], "gate_problems": problems}, indent=1), flush=True)
    if problems or args.sample_only:
        return 1 if problems else 0
    rest = run(bulk_id, todo[args.sample:])
    print(json.dumps({"batch_metrics": collect([], started)["summary"],
                      "failed": [r for r in sample + rest if r["status"] != "completed"]}, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
