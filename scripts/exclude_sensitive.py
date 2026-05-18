"""One-time: move already-synced sensitive/non-itinerary files out of itineraries/.

Files matching the sync exclusion rules (passports, invoices, scans, admin
docs) are moved into _excluded/ at the repo root. That folder is gitignored,
so the files stay on your machine but never reach GitHub. Safe to re-run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from itinerary_planner.index import ITINERARIES_DIR
from itinerary_planner.sync_drive import is_excluded

EXCLUDED_DIR = Path(__file__).resolve().parent.parent / "_excluded"


def main() -> None:
    EXCLUDED_DIR.mkdir(exist_ok=True)
    moved = 0
    for path in sorted(ITINERARIES_DIR.iterdir()):
        if path.is_file() and is_excluded(path.name):
            dest = EXCLUDED_DIR / path.name
            shutil.move(str(path), str(dest))
            print(f"  moved out  {path.name}")
            moved += 1
    print(f"\nDone: {moved} sensitive/non-itinerary file(s) moved to _excluded/.")
    print("These stay local and are gitignored. Now commit + push itineraries/.")


if __name__ == "__main__":
    main()
