"""Enrich the merged contacts list with email / website / WhatsApp / phone
using Apify actors.

Pipeline:
  1. Split rows into "Page-like" FB URLs and "personal profile" URLs.
  2. Pass 1 — apify/facebook-pages-scraper on Page-like URLs to grab
     email, website, phone, whatsapp from the public "About" section.
  3. Pass 2 — for rows still missing email/whatsapp but with a website,
     run apify/contact-info-scraper on the website to pull contact details.
  4. Write output/contacts_enriched.csv with columns:
        #, Name, FacebookURL, Email, Website, WhatsApp, Phone, Source, Notes
     Missing values → "NA".

Usage:
    export APIFY_TOKEN=apify_api_xxx
    python3 scripts/enrich_apify.py

Optional env:
    APIFY_PAGES_ACTOR    (default: apify/facebook-pages-scraper)
    APIFY_CONTACT_ACTOR  (default: vdrmota/contact-info-scraper)
    SKIP_CONTACT_PASS=1  (skip pass 2)
    LIMIT=N              (process only first N rows; useful for a dry run)
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_FILE = ROOT / "input" / "contacts.csv"
OUT_FILE = ROOT / "output" / "contacts_enriched.csv"

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
PAGES_ACTOR = os.environ.get("APIFY_PAGES_ACTOR", "apify/facebook-pages-scraper")
CONTACT_ACTOR = os.environ.get("APIFY_CONTACT_ACTOR", "vdrmota/contact-info-scraper")
SKIP_CONTACT_PASS = os.environ.get("SKIP_CONTACT_PASS") == "1"
LIMIT = int(os.environ.get("LIMIT", "0") or 0)

API_BASE = "https://api.apify.com/v2"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
WA_LINK_RE = re.compile(r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)/?([+\d]+)")
PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")


def _req(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict:
    if not APIFY_TOKEN:
        sys.exit("APIFY_TOKEN env var is required.")
    url = f"{API_BASE}{path}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}token={urllib.parse.quote(APIFY_TOKEN)}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_actor_sync(actor_id: str, input_payload: dict, wait_secs: int = 600) -> list[dict]:
    """Start an actor run and poll until it finishes, then return dataset items."""
    actor_slug = actor_id.replace("/", "~")
    run = _req("POST", f"/acts/{actor_slug}/runs", body=input_payload)["data"]
    run_id = run["id"]
    dataset_id = run["defaultDatasetId"]

    deadline = time.time() + wait_secs
    status = run["status"]
    while status in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise TimeoutError(f"Actor {actor_id} run {run_id} timed out")
        time.sleep(5)
        status = _req("GET", f"/actor-runs/{run_id}")["data"]["status"]

    if status != "SUCCEEDED":
        raise RuntimeError(f"Actor {actor_id} run {run_id} ended with status {status}")

    items = _req("GET", f"/datasets/{dataset_id}/items?clean=true&format=json")
    return items if isinstance(items, list) else []


def is_personal_profile(url: str) -> bool:
    if not url:
        return False
    return "/profile.php?id=" in url or bool(re.search(r"facebook\.com/people/", url))


def canonical_fb(url: str) -> str:
    return (url or "").strip().rstrip("/")


def extract_whatsapp(*fields: str) -> str:
    for f in fields:
        if not f:
            continue
        m = WA_LINK_RE.search(f)
        if m:
            return m.group(1)
    return ""


def first_email(*fields: str) -> str:
    for f in fields:
        if not f:
            continue
        m = EMAIL_RE.search(f)
        if m:
            return m.group(0)
    return ""


def first_phone(*fields: str) -> str:
    for f in fields:
        if not f:
            continue
        m = PHONE_RE.search(f)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return ""


def normalize_pages_item(item: dict) -> dict:
    """Map various field name conventions used by FB page scrapers to a common shape."""
    url = item.get("pageUrl") or item.get("url") or item.get("facebookUrl") or ""
    email = item.get("email") or first_email(json.dumps(item, ensure_ascii=False))
    website = item.get("website") or item.get("websites") or ""
    if isinstance(website, list):
        website = website[0] if website else ""
    phone = item.get("phone") or item.get("phoneNumber") or ""
    whatsapp = item.get("whatsapp") or extract_whatsapp(
        item.get("messengerLink", ""),
        item.get("website", "") if isinstance(item.get("website"), str) else "",
        json.dumps(item, ensure_ascii=False),
    )
    return {
        "url": canonical_fb(url),
        "email": email or "",
        "website": website or "",
        "phone": phone or "",
        "whatsapp": whatsapp or "",
    }


def load_rows() -> list[dict]:
    rows: list[dict] = []
    with IN_FILE.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({"#": r["#"], "Name": r["Name"], "FacebookURL": r["FacebookURL"].strip()})
    if LIMIT > 0:
        rows = rows[:LIMIT]
    return rows


def pass_facebook_pages(rows: list[dict]) -> dict[str, dict]:
    targets = [r["FacebookURL"] for r in rows if r["FacebookURL"] and not is_personal_profile(r["FacebookURL"])]
    targets = list(dict.fromkeys(targets))
    if not targets:
        return {}
    print(f"[pass1] facebook-pages-scraper on {len(targets)} URLs via {PAGES_ACTOR}")
    payload = {"startUrls": [{"url": u} for u in targets], "resultsLimit": 1}
    items = run_actor_sync(PAGES_ACTOR, payload)
    print(f"[pass1] got {len(items)} items")
    out: dict[str, dict] = {}
    for item in items:
        norm = normalize_pages_item(item)
        if norm["url"]:
            out[norm["url"].lower()] = norm
    return out


def pass_contact_scraper(websites: list[str]) -> dict[str, dict]:
    websites = list(dict.fromkeys([w for w in websites if w]))
    if not websites or SKIP_CONTACT_PASS:
        return {}
    print(f"[pass2] contact-info-scraper on {len(websites)} websites via {CONTACT_ACTOR}")
    payload = {"startUrls": [{"url": w} for w in websites], "maxDepth": 1, "maxRequestsPerStartUrl": 5}
    items = run_actor_sync(CONTACT_ACTOR, payload)
    print(f"[pass2] got {len(items)} items")
    out: dict[str, dict] = {}
    for item in items:
        url = item.get("url") or item.get("domain") or ""
        emails = item.get("emails") or []
        phones = item.get("phones") or item.get("phoneNumbers") or []
        whatsapp = ""
        for src in (item.get("whatsapps") or []), (item.get("socialHandles") or {}).get("whatsapp", []):
            if isinstance(src, list) and src:
                whatsapp = extract_whatsapp(*[str(s) for s in src]) or (src[0] if src else "")
                if whatsapp:
                    break
        out[url] = {
            "email": emails[0] if emails else "",
            "phone": phones[0] if phones else "",
            "whatsapp": whatsapp or "",
        }
    return out


def na(v: str) -> str:
    v = (v or "").strip()
    return v if v else "NA"


def main() -> None:
    rows = load_rows()
    print(f"Loaded {len(rows)} rows from {IN_FILE}")

    pages_results = pass_facebook_pages(rows)

    # Build the first-pass enriched view
    enriched: list[dict] = []
    websites_needing_followup: list[str] = []
    for r in rows:
        url = r["FacebookURL"]
        info = pages_results.get(url.lower(), {})
        e = {
            "#": r["#"],
            "Name": r["Name"],
            "FacebookURL": url or "",
            "Email": info.get("email", ""),
            "Website": info.get("website", ""),
            "WhatsApp": info.get("whatsapp", ""),
            "Phone": info.get("phone", ""),
            "Source": "facebook-page" if info else "",
            "Notes": "",
        }
        if not url:
            e["Notes"] = "no FB URL provided"
        elif is_personal_profile(url):
            e["Notes"] = "personal profile — FB usually hides contact info"
        elif not info:
            e["Notes"] = "no data returned from FB page scraper"
        if e["Website"] and not (e["Email"] and e["WhatsApp"]):
            websites_needing_followup.append(e["Website"])
        enriched.append(e)

    contact_results = pass_contact_scraper(websites_needing_followup)

    for e in enriched:
        site = e["Website"]
        if not site:
            continue
        # contact-info-scraper keys are the crawled URLs, which may include path —
        # do a startswith match on the original site root
        match = next((v for k, v in contact_results.items() if k.startswith(site)), None)
        if not match:
            continue
        if not e["Email"] and match.get("email"):
            e["Email"] = match["email"]
            e["Source"] = (e["Source"] + "+website") if e["Source"] else "website"
        if not e["WhatsApp"] and match.get("whatsapp"):
            e["WhatsApp"] = match["whatsapp"]
        if not e["Phone"] and match.get("phone"):
            e["Phone"] = match["phone"]

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["#", "Name", "FacebookURL", "Email", "Website", "WhatsApp", "Phone", "Source", "Notes"])
        for e in enriched:
            w.writerow([
                e["#"],
                e["Name"],
                na(e["FacebookURL"]),
                na(e["Email"]),
                na(e["Website"]),
                na(e["WhatsApp"]),
                na(e["Phone"]),
                na(e["Source"]),
                e["Notes"] or "",
            ])

    have_email = sum(1 for e in enriched if e["Email"])
    have_wa = sum(1 for e in enriched if e["WhatsApp"])
    have_site = sum(1 for e in enriched if e["Website"])
    print(f"Wrote {OUT_FILE}")
    print(f"  email: {have_email}/{len(enriched)}  whatsapp: {have_wa}/{len(enriched)}  website: {have_site}/{len(enriched)}")


if __name__ == "__main__":
    main()
