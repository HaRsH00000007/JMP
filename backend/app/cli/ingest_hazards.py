"""Ingest the 25-hazard Excel into the database.

    python -m app.cli.ingest_hazards [--file PATH] [--version 1.0] [--no-activate]

Idempotent for the same file+version; refuses a different file under an existing version.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.db.session import session_scope
from app.errors import JmpError
from app.services import hazard_library
from app.settings import settings


def main(argv: list[str] | None = None) -> int:
    s = settings()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", type=Path, default=s.hazard_source_xlsx)
    ap.add_argument("--version", default=s.hazard_library_version)
    ap.add_argument("--no-activate", action="store_true")
    args = ap.parse_args(argv)
    try:
        with session_scope() as db:
            lib = hazard_library.ingest(db, args.file, args.version, activate=not args.no_activate)
            applied = hazard_library.apply_approved_short_controls(db, lib)
            print(json.dumps({"version": lib.version, "source_sha256": lib.source_sha256, "active": lib.is_active,
                              "hazards": len(lib.hazards), "approved_short_controls_applied": applied}))
    except JmpError as exc:
        print(json.dumps({"error": exc.message, "details": exc.details}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
