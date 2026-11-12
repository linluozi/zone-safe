"""CLI: python -m zone_safe LOCAL ZONE [--choose strict|earlier|later]."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from .core import (
    AmbiguousTimeError,
    NonexistentTimeError,
    ZoneSafeError,
    classify,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zone_safe",
        description="Classify a naive wall-clock time as unique/ambiguous/nonexistent.",
    )
    parser.add_argument("local", help="naive local time, e.g. 2026-11-01T01:30")
    parser.add_argument("zone", help="IANA zone, e.g. America/New_York")
    parser.add_argument(
        "--choose",
        choices=("strict", "earlier", "later"),
        default="strict",
        help="fold policy (gaps always fail)",
    )
    args = parser.parse_args(argv)

    try:
        local = datetime.fromisoformat(args.local)
    except ValueError as exc:
        print(f"invalid local datetime: {exc}", file=sys.stderr)
        return 2
    try:
        resolution = classify(local, args.zone)
        chosen = resolution.resolve(args.choose)
    except AmbiguousTimeError as exc:
        print(json.dumps(_payload(exc.resolution), indent=2))
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except NonexistentTimeError as exc:
        print(json.dumps(_payload(exc.resolution), indent=2))
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except ZoneSafeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = _payload(resolution)
    payload["chosen_utc"] = chosen.isoformat()
    print(json.dumps(payload, indent=2))
    return 0


def _payload(resolution) -> dict:
    return {
        "local": resolution.local.isoformat(),
        "zone": resolution.zone,
        "kind": resolution.kind,
        "candidates_utc": [c.isoformat() for c in resolution.candidates],
        "projections_local": [p.isoformat() for p in resolution.projections],
    }


if __name__ == "__main__":
    raise SystemExit(main())
