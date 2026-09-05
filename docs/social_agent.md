# North Nepal Social Engagement Agent — V1

An AI assistant that watches comments on the company's **Facebook** and
**Instagram** posts, understands each one, decides whether it should be
answered, drafts a short reply in the company's voice, and routes it to a human
when a human is needed.

The goal is not a chatbot. It is a system that behaves like a member of the
North Nepal team — brief, warm, correct, and silent when silence is right.

---

## 1. What is actually built

| Layer | Status |
| --- | --- |
| OpenAI decision service (structured output + validation) | Implemented |
| Externalised system prompt | Implemented — `social_agent/ai/prompts/system_prompt.md` |
| Apify client (documented REST API only) | Implemented |
| Facebook / Instagram read adapters | Implemented, needs your actor ids + page URLs |
| Facebook / Instagram reply publishing | Implemented, **needs a reply actor you supply** |
| Guardrails (safety, complaints, invented facts, voice, length, repetition) | Implemented |
| Duplicate protection | Implemented, enforced at the database level |
| Dry run | Implemented, **on by default** |
| Human approval workflow | Implemented, **required by default** |
| Auto reply | Implemented, **disabled by default** |
| Knowledge base + CRUD | Implemented |
| Admin dashboard | Implemented (WSGI) |
| Audit log / structured logging | Implemented |
| Tests | 60 tests, all passing, no network required |

### What is simulated, and what still needs configuration

Read this before treating anything as production-ready.

1. **No live OpenAI call has been verified from the build environment.** The
   sandbox this was built in blocks `api.openai.com` at the network policy
   level, so the request-building, response-parsing, validation, retry and
   fallback paths are all covered by tests against a fake HTTP transport, but
   an end-to-end call to the real API has not been observed. **Run
   `python -m social_agent status` and one `run-once` on your own machine
   before trusting it.** The failure path is verified: when OpenAI is
   unreachable the agent escalates every comment to a human and publishes
   nothing.

2. **No Apify actor has been run.** The client implements Apify's documented
   platform API (start a run → poll → read the dataset), which is the same
   pattern the existing `scripts/enrich_apify.py` in this repo uses
   successfully. What is *not* verified is the input and output shape of any
   particular scraper actor, because those are defined by the actor's author
   and change between versions. See §6.

3. **Reply publishing has no default actor.** Posting a reply requires an actor
   with authenticated write access to your page. Rather than guess at an API
   contract, `APIFY_FACEBOOK_REPLY_ACTOR` and `APIFY_INSTAGRAM_REPLY_ACTOR` are
   empty and the adapter raises a clear error naming what is missing. **Until
   you configure one, the agent is read-only**: it will fetch, classify, draft
   and queue, but the final publish step will fail loudly rather than silently
   pretend.

4. **`python -m social_agent demo` uses a scripted stand-in for the model**, not
   OpenAI. It exists to exercise the pipeline offline. Its replies are canned.

---

## 2. Architecture

```
Facebook ─┐                                          ┌─ Facebook
          ├─ Apify ─ adapters ─ agent ─ OpenAI ─ guardrails ─ policy ─┤
Instagram ┘                       │                   │              └─ Instagram
                                  │                   │
                          knowledge base      human approval OR auto-reply
```

One AI agent, not one per platform. The platform is a field in the context sent
to the model, so behaviour is channel-aware without duplicated prompts.

```
social_agent/
  config/        settings; the only module that reads os.environ
  database/      SQLite schema, migrations, repositories
  ai/            OpenAI client, decision service, output schema, prompts/
  apify/         Apify REST client, actor registry, input templates
  social/        SocialPlatformAdapter + Facebook / Instagram / Mock
  knowledge/     approved-knowledge filtering and seed data
  agent/         pipeline, guardrails, routing policy, publisher, variation
  dashboard/     WSGI app, JSON API, auth, single-page UI
  observability/ structured logging with a per-cycle trace id
  app.py         composition root
  testing.py     test doubles (never a runtime AI provider)
```

**The AI layer does not know Apify exists.** It receives `FetchedPost` and
`FetchedComment` values. Replacing Apify with the Meta Graph API means writing
one new adapter class; nothing in `ai/` or `agent/` changes.

### Dependencies

The Python standard library, plus `pytest` to run the tests. That is the whole
list. No SDK, no web framework, no ORM.

---

## 3. Setup

Requires Python 3.11+.

```bash
cp .env.example .env          # then edit .env
python -m social_agent init   # create the database, seed the knowledge base
python -m social_agent status # check what is and isn't configured
```

Minimum to run for real:

```bash
OPENAI_API_KEY="sk-..."
APIFY_API_TOKEN="apify_api_..."
FACEBOOK_PAGE_URL="https://www.facebook.com/yourpage"
INSTAGRAM_PROFILE_URL="https://www.instagram.com/yourprofile/"
DASHBOARD_PASSWORD="something-long"
```

---

## 4. Local development

```bash
python -m pytest tests/test_social_agent.py -q   # 60 tests, no network needed
python -m social_agent demo                      # offline walkthrough
python -m social_agent serve                     # dashboard on :8080
```

The demo runs the real pipeline over the bundled sample comments with a scripted
AI stand-in, and prints what the agent decided for each one.

---

## 5. Dry run — start here

V1 ships with `DRY_RUN=true`. In dry run the agent:

- fetches posts and comments,
- stores them (duplicates dropped),
- sends each to OpenAI,
- validates the decision and applies guardrails,
- stores the drafted reply,
- shows everything in the dashboard,

and **publishes nothing**.

Try it with no credentials at all:

```bash
USE_MOCK_SOCIAL_DATA=true python -m social_agent demo
```

Then with your real OpenAI key against the sample comments — real model, no
social accounts touched:

```bash
USE_MOCK_SOCIAL_DATA=true DRY_RUN=true python -m social_agent run-once
python -m social_agent pending
```

This is the right way to read what the model actually says in your voice before
any of it is public.

Going live is two deliberate steps:

1. `DRY_RUN=false` — approved replies now really post. Approval is still
   required for every one.
2. `HUMAN_APPROVAL_REQUIRED=false` **and** `AUTO_REPLY_ENABLED=true` — the agent
   may post on its own, but only when *every* condition in §7 holds.

Do not do both at once.

---

## 6. Configuring Apify actors

Actor ids are configuration, and their input schemas belong to their authors.
Two things to set per operation:

1. The actor id — `APIFY_<PLATFORM>_<OPERATION>_ACTOR` in `.env`, or the
   Settings tab in the dashboard.
2. The input template — `social_agent/apify/actor_inputs.json`.

Templates use `{placeholders}` filled at runtime: `{page_urls}`, `{post_urls}`,
`{limit}`, `{comment_url}`, `{reply_text}`, `{comment_id}`, `{post_id}`.

```json
"facebook_comments": {
  "startUrls": ["{post_urls}"],
  "resultsLimit": "{limit}"
}
```

Check the field names on the actor's own page before running it against a real
page — the defaults follow what the named actors document, but actor inputs do
change.

Comment normalisation reads several candidate key names for each field, so a
scraper that calls it `text` and one that calls it `message` both work. **A
comment with no id is dropped**, never given a synthetic one: a fabricated id
would defeat duplicate protection.

---

## 7. When the agent may reply on its own

Every one of these must hold, or it goes to a human instead:

```
action == "reply"
AND risk_level == "low"
AND needs_human == false
AND confidence >= AUTO_REPLY_MIN_CONFIDENCE   (default 0.90)
AND auto_reply_enabled
AND NOT human_approval_required
AND no reply already exists for this comment
AND NOT dry_run
```

The dashboard shows which specific condition stopped a reply.

---

## 8. Guardrails

The model is instructed, but it is not the last word. Guardrails run after every
decision and can only make the outcome **more** conservative — they can never
turn an escalation into a reply or raise a confidence score.

| Guardrail | Behaviour |
| --- | --- |
| Current incident | Landslide, flood, rescue, "is the trail open?", "are flights running?" → forced escalate, risk `high`, no reply |
| Complaint / refund | Scam, refund, legal threat → forced escalate, never argued with |
| Invented figures | A price or date in the reply that is not in approved knowledge → blocked |
| Brand voice | "Thank you for reaching out", "BOOK NOW" and similar → flagged for a human |
| Length | Max 3 sentences, trimmed to `MAX_REPLY_LENGTH` on a sentence boundary |
| Emoji | At most 2 |
| Repetition | Too close to a recent reply → flagged for a human, confidence capped |

The safety check errs toward escalation on purpose. A needless escalation costs
a human a minute; a wrong reassurance about a mountain costs more.

---

## 9. Duplicate protection

Mandatory, and enforced in four places rather than trusted to one:

1. `UNIQUE (platform, platform_comment_id)` — a comment is stored once.
2. `claim_new()` moves `new → processing` with a conditional UPDATE, so two
   overlapping cycles cannot both claim the same comment.
3. `has_posted_reply()` is checked before publishing, and the adapter is asked
   whether the platform already shows a reply from us.
4. A partial unique index (`posted = 1`) means the database itself refuses a
   second posted reply per comment.

A posting failure is **never** retried automatically — the reply may in fact
have landed. It stays visible in the dashboard for a person to decide.

Verified: running three cycles over 13 sample comments stores 13 comments and
makes 13 OpenAI calls, not 39.

---

## 9a. Watching the spend

Every OpenAI call is recorded in the `ai_usage` table — tokens in, tokens out,
model, attempt count, and whether the output was usable. Failed and retried
calls are counted too, because a retry costs money whether or not its output
was any good.

The Overview tab shows calls, tokens (total and rolling 24h), and estimated
cost. **Cost is only shown once you enter your own prices**, per 1,000,000
tokens, in Settings or via `OPENAI_INPUT_COST_PER_MILLION` /
`OPENAI_OUTPUT_COST_PER_MILLION`. With no rate set the dashboard says so rather
than showing a figure from a price list that may be out of date.

### The daily cap

`DAILY_TOKEN_BUDGET` (0 = off) is a hard stop. Once that many tokens have been
used in a rolling 24 hours, `run_cycle` stops claiming comments, logs a
`budget.exceeded` audit entry, and reports `budget_stopped` in the cycle
result. Comments are not lost — they stay `new` and are picked up once the
window rolls forward or the cap is raised.

This exists for the unattended case: `python -m social_agent watch` running on a
server should stop costing money on its own if something goes wrong, rather than
running until somebody notices the bill.

Rough sizing from the bundled sample run: **13 comments ≈ 5,980 tokens**, or
about 460 tokens per comment. Multiply by your own rate to size a cap.

---

## 10. Knowledge base

The only company-specific facts the model may state are the ones in the
knowledge base. An item reaches the prompt only if it is `active`, inside its
validity window, and not `human_only`.

The seed data deliberately contains **no prices, dates or permit fees** — those
go stale, and a stale number in a public comment is worse than no answer. Add
them through the dashboard once they are current and approved.

`human_only` items (internal margins, escalation contacts) are never sent to the
model at all. There is a test for this.

---

## 11. Dashboard

`python -m social_agent serve` → <http://127.0.0.1:8080>

Overview · Pending Approval · Inbox · Reply History · Knowledge Base · Settings ·
Logs.

Pending Approval offers **Approve & Reply**, **Edit & Reply**, **Ignore** and
**Escalate**. Edited text keeps the original alongside it, so you can see what
the model said and what actually went out.

Protected by HTTP Basic auth. With `DASHBOARD_AUTH_ENABLED=true` and no password
set, the dashboard **refuses to start** rather than serving unauthenticated.

API keys are never editable or visible here — the Settings tab reports only
whether each is configured.

---

## 12. Production deployment

The dashboard is a WSGI app, so use a real server:

```bash
pip install gunicorn
gunicorn --workers 2 --bind 127.0.0.1:8080 \
  'social_agent.dashboard.app:create_wsgi_app()'
```

Put it behind nginx or Caddy terminating TLS. Never expose port 8080 directly.

Run the poller as a separate long-lived process:

```ini
# /etc/systemd/system/north-nepal-agent.service
[Service]
WorkingDirectory=/opt/north-nepal
EnvironmentFile=/opt/north-nepal/.env
ExecStart=/usr/bin/python3 -m social_agent watch
Restart=always
User=northnepal
```

Checklist:

- [ ] `.env` is `chmod 600`, owned by the service user, and not in git
- [ ] `DASHBOARD_PASSWORD` is long and random
- [ ] `LOG_FORMAT=json` and logs shipped somewhere
- [ ] `DATABASE_PATH` on persistent disk, and backed up — it holds the audit trail
- [ ] Ran in dry run for long enough to read what the model actually says
- [ ] A person is genuinely watching the escalation queue

Note that SQLite is a good fit for one poller plus one dashboard on one box. If
you later want several pollers on separate machines, move to Postgres — the
repository layer is the only thing that would change.

---

## 13. Extending

**Another platform** (Messenger, WhatsApp, Google Business Profile): implement
`SocialPlatformAdapter` and register it in `social/registry.py`. The AI service,
guardrails and pipeline are untouched. Deliberately not implemented in V1.

**Changing the voice:** edit `social_agent/ai/prompts/system_prompt.md`. It is
re-read when it changes on disk — no restart, no code change.

**A new intent:** add it to `INTENTS` in `database/models.py` and mention it in
the prompt. Unknown intents from the model are preserved, not rejected.

---

## 14. Troubleshooting

| Symptom | Cause |
| --- | --- |
| Everything is escalated with "AI unavailable" | `OPENAI_API_KEY` missing or the model name is one your account cannot use |
| "No Apify posts actor configured" | Set `APIFY_<PLATFORM>_POSTS_ACTOR` |
| "No Apify reply actor configured" | Expected until you supply one — see §1.3 |
| Replies generated but never posted | `DRY_RUN` is still `true` |
| Nothing auto-replies | Check the Overview tab; it names the blocking condition |
| Dashboard will not start | `DASHBOARD_PASSWORD` is not set |
| Comments fetched but none processed | They were already seen — duplicate protection working |
