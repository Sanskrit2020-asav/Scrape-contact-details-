"""Merge the Excel-XML and CSV exports of the Facebook following list into a
single deduplicated CSV with columns: #, Name, FacebookURL.

Run:
    python3 scripts/merge_inputs.py
"""
from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
XML_FILE = ROOT / "input" / "following_list.xml"
CSV_FILE = ROOT / "input" / "following_list_173_264.csv"
OUT_FILE = ROOT / "input" / "contacts.csv"

NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}


def parse_xml(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    tree = ET.parse(path)
    root = tree.getroot()
    for row in root.iter(f"{{{NS['ss']}}}Row"):
        cells = [
            (c.find(f"{{{NS['ss']}}}Data").text or "").strip()
            for c in row.findall(f"{{{NS['ss']}}}Cell")
            if c.find(f"{{{NS['ss']}}}Data") is not None
        ]
        if len(cells) < 3:
            continue
        idx, name, url = cells[0], cells[1], cells[2]
        if idx == "#" or not name:
            continue
        rows.append((name, url))
    return rows


def parse_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            name = (r.get("Name") or "").strip()
            url = (r.get("Facebook Profile URL") or r.get("Profile URL") or "").strip()
            if not name:
                continue
            rows.append((name, url))
    return rows


def canonical_url(url: str) -> str:
    if not url:
        return ""
    u = url.strip().rstrip("/").lower()
    u = re.sub(r"^https?://(www\.|m\.|web\.)?facebook\.com/", "https://www.facebook.com/", u)
    return u


def main() -> None:
    pairs = parse_xml(XML_FILE) + parse_csv(CSV_FILE)

    seen_urls: set[str] = set()
    seen_names: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for name, url in pairs:
        key_url = canonical_url(url)
        key_name = name.lower()
        if key_url and key_url in seen_urls:
            continue
        if not key_url and key_name in seen_names:
            continue
        if key_url:
            seen_urls.add(key_url)
        seen_names.add(key_name)
        deduped.append((name, url))

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["#", "Name", "FacebookURL"])
        for i, (name, url) in enumerate(deduped, start=1):
            w.writerow([i, name, url])

    with_url = sum(1 for _, u in deduped if u)
    print(f"Wrote {OUT_FILE} — {len(deduped)} rows ({with_url} with URL, {len(deduped) - with_url} without)")


if __name__ == "__main__":
    main()
