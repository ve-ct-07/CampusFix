"""Group two existing reports after reviewing a duplicate match."""

import argparse
import sys
from pathlib import Path

# Add CampusFix project root to Python's module search path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.db import (
    initialize_database,
    get_issue,
    attach_duplicate_report,
)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--primary",
        type=int,
        required=True,
        help="Primary issue ID",
    )

    parser.add_argument(
        "--related",
        type=int,
        required=True,
        help="Related issue ID",
    )

    args = parser.parse_args()

    initialize_database()

    primary = get_issue(args.primary)
    related = get_issue(args.related)

    if primary is None or related is None:
        raise SystemExit("One or both issue IDs do not exist.")

    if args.primary == args.related:
        raise SystemExit("An issue cannot be grouped with itself.")

    if (
        primary["location"].strip().casefold()
        != related["location"].strip().casefold()
    ):
        raise SystemExit(
            "Locations differ. Refusing to group these reports."
        )

    group_id = attach_duplicate_report(
        args.related,
        args.primary,
    )

    print(f"Incident group: GF-{group_id:04d}")
    print(f"Primary report: CF-{args.primary:04d}")
    print(f"Related report: CF-{args.related:04d}")
    print("Both issue records remain intact.")


if __name__ == "__main__":
    main()