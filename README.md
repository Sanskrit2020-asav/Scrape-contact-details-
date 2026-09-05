This repository contains three independent tools:

1. **Social Engagement Agent** for North Nepal Travel & Trek — monitors comments
   on Facebook and Instagram, classifies each one with **OpenAI**, drafts a
   short reply in the company's voice, and routes it to a human when a human is
   needed. Social I/O runs through **Apify**. Ships in dry-run mode with human
   approval required and auto-reply disabled. See
   **[`docs/social_agent.md`](docs/social_agent.md)** and the `social_agent/`
   package. Quick start:

   ```
   python -m social_agent demo        # offline walkthrough, no credentials
   python -m social_agent serve       # admin dashboard on :8080
   ```

2. **Intelligent Sales Assistant** for North Nepal Travel & Trek — an AI
   consultant that estimates trek costs, retrieves company knowledge (RAG),
   researches the website/web, reasons like a 15-year Nepal trekking expert, and
   drafts quotations and emails. See **[`docs/sales_assistant.md`](docs/sales_assistant.md)**
   and the `sales_assistant/` package. Quick start:
   `python -m sales_assistant.cli estimate --trek "Everest Base Camp" --days 14 --pax 2`
3. **Facebook contact scraper** (below) — the original lead-enrichment pipeline.

---

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
