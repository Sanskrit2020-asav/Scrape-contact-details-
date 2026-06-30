# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-stage data pipeline that pulls contact details (email / website /
WhatsApp / phone) for a list of Facebook profiles and Pages by calling Apify
actors over their REST API. Pure Python 3 standard library — no third-party
dependencies, no `requirements.txt`, no virtualenv, no build step, and no test
suite or linter configured.

## Commands

```bash
# Stage 1: merge the two raw exports into input/contacts.csv
python3 scripts/merge_inputs.py

# Stage 2: enrich via Apify (requires a token)
export APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxx
python3 scripts/enrich_apify.py

# Dry run / sizing — process only the first N rows
LIMIT=10 python3 scripts/enrich_apify.py

# Skip the website follow-up pass (pass 2)
SKIP_CONTACT_PASS=1 python3 scripts/enrich_apify.py
```

Other env overrides: `APIFY_PAGES_ACTOR` (default `apify/facebook-pages-scraper`),
`APIFY_CONTACT_ACTOR` (default `vdrmota/contact-info-scraper`).

## Pipeline architecture

The two scripts are sequential and coupled by file: `merge_inputs.py` must run
first and produces `input/contacts.csv`, which `enrich_apify.py` consumes.

**`scripts/merge_inputs.py`** — combines two heterogeneous source formats:
`input/following_list.xml` (Excel-XML SpreadsheetML, rows 1–172, parsed with
the `urn:schemas-microsoft-com:office:spreadsheet` namespace) and
`input/following_list_173_264.csv` (rows 173–264). Dedup is two-keyed:
URL-deduped via `canonical_url()` (lowercases, strips trailing slash, and
normalizes `m.`/`web.`/`www.` host variants to `https://www.facebook.com/`);
rows lacking a URL fall back to case-folded-name dedup.

**`scripts/enrich_apify.py`** — two-pass enrichment:

1. *Pass 1* splits rows into "Page-like" vs "personal profile" URLs
   (`is_personal_profile()` — true for `/profile.php?id=` and
   `facebook.com/people/`). Personal profiles are deliberately **not** scraped
   (FB hides their contact info) and are flagged in the `Notes` column. Page
   URLs go to the pages actor.
2. *Pass 2* runs the contact-info actor only on websites discovered in pass 1
   where email **or** WhatsApp is still missing, then back-fills.

Actors are invoked synchronously by `run_actor_sync()`: `POST` to start the
run, poll `/actor-runs/{id}` every 5s until terminal status, then fetch
`/datasets/{id}/items`. Default timeout is 600s per run.

Key robustness detail: `normalize_pages_item()` exists because different FB
page-scraper actors return inconsistent field names. It tries known keys
(`pageUrl`/`url`/`facebookUrl`, `website`/`websites`, `phone`/`phoneNumber`,
etc.) and, as a last resort, regex-scans the entire JSON-serialized item for
emails and `wa.me`/`api.whatsapp.com` links. When swapping actors, extend the
key lists here rather than assuming a fixed schema. Pass-2 results are matched
to rows by `startswith` on the website root because the contact actor keys
results by crawled URL (which may include a path).

Output is `output/contacts_enriched.csv` with columns
`#, Name, FacebookURL, Email, Website, WhatsApp, Phone, Source, Notes`. Missing
values are written as the literal string `NA` (via `na()`), except `Notes`
which is left blank. `Source` records provenance (`facebook-page`,
`website`, or `facebook-page+website`).

## Conventions & gotchas

- Paths are resolved relative to the repo root via `Path(__file__).resolve().parents[1]`, so scripts can be run from any directory.
- `output/*.csv` is gitignored (only `output/.gitkeep` is tracked); generated CSVs are not committed. `input/contacts.csv` is generated but tracked.
- `output/urls_pages.txt`, `urls_personal.txt`, and `no_url_names.txt` are pre-split URL lists used as Apify Pages Scraper inputs.
- Anything written to `NA` downstream means "not found", not an error — personal profiles legitimately end up mostly `NA`.
