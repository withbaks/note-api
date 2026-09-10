#!/usr/bin/env python3
"""Sweep confidence thresholds against golden helper fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from note_core.sync_constants import CONFIDENCE_BY_KIND, REVIEW_CONFIDENCE_THRESHOLD

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "helpers_golden.jsonl"


def main() -> None:
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    print("Per-kind auto-accept thresholds:")
    for key, threshold in sorted(CONFIDENCE_BY_KIND.items()):
        matching = [r for r in rows if r.get("helper_key") == key]
        accepted = sum(1 for r in matching if r.get("expected_status") == "accepted")
        print(f"  {key}: {threshold:.2f}  (golden accepted {accepted}/{len(matching)})")
    print(f"\nReview floor: {REVIEW_CONFIDENCE_THRESHOLD}")
    print(f"Golden rows: {len(rows)}")


if __name__ == "__main__":
    main()
