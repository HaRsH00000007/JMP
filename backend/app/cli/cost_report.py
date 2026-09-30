"""Claude cost metrics for a batch or a time window, compared with the first real batch's baseline.

    python -m app.cli.cost_report --bulk <bulk_job_id> [--bulk <id> ...]
    python -m app.cli.cost_report --since "2026-09-25 00:00"          (UTC)

Per generation job: model(s), calls, retries, tokens (input / cache read / cache write / output), cost, and the
final PDF status. Totals: cost per successful PDF, output tokens and calls per PDF, retry rate, cache hit rate,
and the spend that produced no delivered PDF.
"""

from __future__ import annotations

import argparse
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.models import BulkJobItem, GenerationJob, GenerationUsage, JmpDocument
from app.db.session import session_scope

# First real batch (R036+, claude-opus-5, 24 Sep 2026): the numbers the optimisation is measured against.
BASELINE = {"cost_per_pdf_usd": 0.40, "calls_per_pdf": 2.3, "output_tokens_per_pdf": 12_275, "usd_to_inr": 88.0}


def collect(bulk_ids: list[str], since: str | None) -> dict[str, Any]:
    with session_scope() as db:
        q = select(GenerationUsage).where(GenerationUsage.provider == "anthropic")
        if bulk_ids:
            q = q.where(GenerationUsage.bulk_job_id.in_([uuid.UUID(b) for b in bulk_ids]))
        if since:
            q = q.where(GenerationUsage.created_at >= since)
        usage = db.scalars(q.order_by(GenerationUsage.created_at)).all()
        jobs: dict[uuid.UUID, dict[str, Any]] = {}
        for u in usage:
            j = jobs.setdefault(u.generation_job_id, {"calls": 0, "retries": 0, "models": set(), "input": 0,
                                                      "cache_read": 0, "cache_write": 0, "output": 0,
                                                      "usd": Decimal("0"), "inr": Decimal("0")})
            j["calls"] += 1
            j["retries"] += 1 if u.attempt > 1 else 0
            j["models"].add(u.model)
            j["input"] += u.input_tokens
            j["cache_read"] += u.cache_read_input_tokens
            j["cache_write"] += u.cache_creation_input_tokens
            j["output"] += u.output_tokens
            j["usd"] += u.estimated_cost_usd or 0
            j["inr"] += u.estimated_cost_inr or 0
        for jid, j in jobs.items():
            g = db.get(GenerationJob, jid)
            item = db.get(BulkJobItem, g.bulk_job_item_id) if g and g.bulk_job_item_id else None
            doc = db.get(JmpDocument, g.document_id) if g and g.document_id else None
            j["route"] = item.route_ref if item else "(individual)"
            j["status"] = ("pdf" if doc and not doc.deleted_at else "withdrawn" if doc else
                           (g.error_code or g.status).lower() if g else "unknown")
            j["models"] = sorted(j["models"])
    ok = [j for j in jobs.values() if j["status"] == "pdf"]
    tot = {k: sum(j[k] for j in jobs.values()) for k in ("calls", "retries", "input", "cache_read", "cache_write",
                                                          "output")}
    usd = sum((j["usd"] for j in jobs.values()), Decimal("0"))
    inr = sum((j["inr"] for j in jobs.values()), Decimal("0"))
    wasted = sum((j["usd"] for j in jobs.values() if j["status"] != "pdf"), Decimal("0"))
    prompt = tot["input"] + tot["cache_read"] + tot["cache_write"]
    n = len(ok)
    summary = {
        "successful_pdfs": n, "jobs_with_calls": len(jobs), "calls": tot["calls"],
        "total_usd": float(round(usd, 4)), "total_inr": float(round(inr, 2)),
        "cost_per_successful_pdf_usd": float(round(usd / n, 4)) if n else None,
        "cost_per_successful_pdf_inr": float(round(inr / n, 2)) if n else None,
        "calls_per_successful_pdf": round(sum(j["calls"] for j in ok) / n, 2) if n else None,
        "output_tokens_per_successful_pdf": round(sum(j["output"] for j in ok) / n) if n else None,
        "retry_rate": round(tot["retries"] / tot["calls"], 3) if tot["calls"] else None,
        "first_call_success_rate": round(sum(1 for j in ok if j["calls"] == 1) / n, 3) if n else None,
        "cache_hit_rate": round(tot["cache_read"] / prompt, 3) if prompt else None,
        "output_share_of_tokens": round(tot["output"] / (prompt + tot["output"]), 3) if prompt else None,
        "spend_without_a_pdf_usd": float(round(wasted, 4)),
    }
    if n:
        b = BASELINE
        per = summary["cost_per_successful_pdf_usd"]
        summary["vs_baseline"] = {
            "baseline_cost_per_pdf_usd": b["cost_per_pdf_usd"], "saved_per_pdf_usd": round(b["cost_per_pdf_usd"] - per, 4),
            "saved_per_pdf_inr": round((b["cost_per_pdf_usd"] - per) * b["usd_to_inr"], 2),
            "reduction_pct": round(100 * (1 - per / b["cost_per_pdf_usd"]), 1),
            "baseline_calls_per_pdf": b["calls_per_pdf"], "baseline_output_tokens_per_pdf": b["output_tokens_per_pdf"]}
    rows = [{"route": j["route"], "status": j["status"], "models": j["models"], "calls": j["calls"],
             "retries": j["retries"], "input": j["input"], "cache_read": j["cache_read"],
             "cache_write": j["cache_write"], "output": j["output"], "usd": float(round(j["usd"], 4))}
            for j in jobs.values()]
    return {"summary": summary, "jobs": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bulk", action="append", default=[], help="bulk job id (repeatable)")
    ap.add_argument("--since", help="UTC timestamp, e.g. '2026-09-25 00:00'")
    args = ap.parse_args(argv)
    print(json.dumps(collect(args.bulk, args.since), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
