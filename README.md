# Scrape Facebook Contact Details

Pull email / website / WhatsApp / phone for a list of Facebook profiles and
Pages, using Apify actors.

## Layout

```
input/
  following_list.xml             # original Excel-XML export (rows 1-172)
  following_list_173_264.csv     # CSV export (rows 173-264)
  contacts.csv                   # merged + deduped (generated)
scripts/
  merge_inputs.py                # builds input/contacts.csv
  enrich_apify.py                # runs Apify actors → output/contacts_enriched.csv
output/
  contacts_enriched.csv          # final result (generated)
```

## 1. Merge the inputs

```
python3 scripts/merge_inputs.py
```

Produces `input/contacts.csv` with columns `#, Name, FacebookURL`,
deduplicated by canonicalised URL and case-folded name.

## 2. Enrich via Apify

```
export APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxx
python3 scripts/enrich_apify.py
```

What it does:

1. Sends all Page-style URLs to `apify/facebook-pages-scraper` and pulls
   `email`, `website`, `phone`, and any `wa.me` link found in the page data.
2. For rows where a website was found but email/WhatsApp is still missing,
   runs `vdrmota/contact-info-scraper` on the website to scrape the contact
   page.
3. Writes `output/contacts_enriched.csv` with:

   `#, Name, FacebookURL, Email, Website, WhatsApp, Phone, Source, Notes`

   Missing values → `NA`. `Notes` flags personal profiles where FB hides
   contact info.

### Useful env vars

| Variable | Default | Purpose |
| --- | --- | --- |
| `APIFY_TOKEN` | _(required)_ | Your Apify API token |
| `APIFY_PAGES_ACTOR` | `apify/facebook-pages-scraper` | Override Page scraper |
| `APIFY_CONTACT_ACTOR` | `vdrmota/contact-info-scraper` | Override website scraper |
| `SKIP_CONTACT_PASS` | _(unset)_ | Set `1` to skip pass 2 |
| `LIMIT` | `0` | Process only the first N rows (dry-run sizing) |

### Quick dry run

```
LIMIT=10 python3 scripts/enrich_apify.py
```

## Expectations

- **Pages & agencies** (e.g. `Ian Taylor Trekking`, `AltiPro Adventures`,
  `Heaven On Nepal`) → high hit-rate for email/website/phone.
- **Personal profiles** (`/profile.php?id=...` or `/people/...`) → mostly
  `NA`; Facebook hides contact info on personal profiles. The script flags
  these in the `Notes` column so you know to DM them instead.
- **WhatsApp** is captured when a `wa.me/<number>` or
  `api.whatsapp.com/send?phone=` link is present in the Page data or on the
  linked website. A bare `Phone` value in a trekking market is usually a
  WhatsApp number too.

---

# Itinerary Planner (separate tool)

A local web app that indexes your **prepared itineraries** (`.pdf`, `.docx`,
`.txt`, `.md`) and matches them to a client's brief. No web scraping in v1 —
it ranks against your own files.

## Setup

```
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Add your itineraries

Drop your prepared itinerary files into the `itineraries/` folder. (Cloud
sessions can't reach your Mac or Google Drive directly, so the files must be
committed/copied here.) Three sample `.docx` files ship as a demo — delete
them once you add your own.

## Run

```
. .venv/bin/activate
python -m itinerary_planner.app
```

Open http://127.0.0.1:5000 — fill in the client's destination, trip length,
travelers, budget, season, and interests. You get the top 5 ranked matches
with a match score, the terms that matched, a snippet, and a download link.
Use **Re-index** after adding or changing files.

## How matching works

Pure-Python TF-IDF cosine similarity over each itinerary's text, with a
duration-proximity boost (a "14 day" request favours ~14-day itineraries).
No external ML dependency.

## Sync itineraries from Google Drive

There is no pre-existing Drive connection — the connector authenticates with
**your** Google credential. Run it where you have that credential (your Mac);
the cloud session cannot reach your Drive.

### One-time credential setup (pick one)

- **Service account** (recommended for a folder you own / shared with it):
  create a service account in Google Cloud, enable the Drive API, download
  its JSON key, and share the Drive folder with the service account's email.
  ```
  export GDRIVE_SERVICE_ACCOUNT=/path/to/service-account.json
  ```
- **OAuth Desktop client** (uses your own Google login, opens a browser once):
  ```
  export GDRIVE_OAUTH_CLIENT=/path/to/oauth_client.json
  ```

### Sync

```
. .venv/bin/activate
python -m itinerary_planner.sync_drive --folder "https://drive.google.com/drive/folders/XXXX"
# or: export GDRIVE_FOLDER_ID=XXXX  then  python -m itinerary_planner.sync_drive
```

Pulls PDF/DOCX/TXT/MD (Google Docs are exported to .docx) into
`itineraries/`, skips unchanged files, then `git commit && git push` so the
files are available everywhere. The web app also has a **Sync from Drive**
button that does the same when credentials are configured on the host.
