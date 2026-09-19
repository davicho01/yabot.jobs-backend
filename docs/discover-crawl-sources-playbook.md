# Discovering new crawl sources by sector

A runbook for proactively growing `crawl_sources` — finding companies that
aren't in yabot.jobs yet and adding them — as opposed to
`docs/adapter-playbook.md`, which works the reactive queue of `pending` rows
that real user submissions create. Written so any LLM (or human) with repo
access, a web-search tool, and shell access can run this end-to-end.

## 0. Point at a target and get a token

Same as `docs/adapter-playbook.md` step 0.5: default to prod
(`https://api.yabot.jobs`), get a personal access token via `POST
/auth/tokens` if you don't already have one for this target, use it as a
bearer token on every call below. Everything in this doc writes to whichever
backend `CRAWL_ADMIN_BASE_URL` points at — confirm with the user before
running against prod if that isn't already the obvious default.

## 1. Pull the current roster for dedup

```bash
curl -s -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources" \
  | jq -r '[.[] | select(.status != "rejected") | .name] | join(", ")'
```

Keep this list (active + pending names) handy — it's the exclusion list every
research pass needs, so you don't re-propose companies already covered.
Re-pull it after each batch you add, since the list grows as you go.

## 2. Pick sectors

If the user names specific companies or sectors, use those. If they say
"all sectors" / "as much as you can", work through broad verticals one or a
few at a time rather than trying everything at once — tech/SaaS, healthcare,
retail/e-commerce, financial services, media/entertainment/gaming,
industrial/logistics/aerospace, travel/hospitality/telecom/energy,
automotive, consumer goods/CPG, biotech/pharma, real estate/proptech,
education/edtech, semiconductor/hardware/networking, plus whatever else is
still uncovered (agriculture, legal, government/public sector, insurance,
staffing, non-profit, ...). Track sectors as tasks (TaskCreate) so progress
survives a long session.

## 3. Research each sector

For a handful of sectors at once, launch one **fork** per sector in
parallel (not a fresh general-purpose agent — a fork shares context and
keeps the raw search noise out of the main conversation). Each fork's
prompt should include:

- The exclusion list from step 1.
- A target of ~15-18 companies for that sector.
- The list of ATS platforms yabot.jobs already has working adapters for
  (read `app/services/adapters/__init__.py`'s `ADAPTERS` list for the
  current set — greenhouse, lever, ashby, workday, eightfold, gem, workable,
  bamboohr, clearcompany, adp, oracle_fusion, amazon, google, apple,
  dynatrace, clinch, jazzhr, fullstack, motion_recruitment, nlx, echo_jobs,
  paycor_recruiting, personio, recruitee, breezyhr, successfactors,
  talentbrew, glidefast, stripe, text — check the file, this list drifts).
- Instructions to prioritize companies on greenhouse/lever/ashby/workday/
  eightfold (fast, common, trivial to add) and verify each board actually
  has live postings right now (not just that the URL shape matches) via
  WebFetch — an empty board isn't worth adding.
- Instructions to include 2-3 companies on a genuinely unsupported platform
  (iCIMS, SmartRecruiters, Taleo, Avature, Phenom, Jobvite, UKG, in-house,
  ...), each with one **specific individual job-posting URL** (not a search
  page) rather than a board root — see step 5 for why.
- Read-only: WebSearch/WebFetch only, no admin/write API calls from the
  fork itself — it reports a table back, the parent (you) does the writes,
  so nothing hits prod without a human-reviewable trail of what was added.

Report format: two tables, "Known ATS — ready to add" (Company | Board URL |
ATS type | Live postings verified | Notes) and "Unsupported platform —
flagged pending" (Company | Example job posting URL | Detected platform |
Notes).

**WebSearch has a session-wide budget shared across every fork**, not a
per-fork allowance. Running many sector forks in parallel burns through it
fast — if a fork reports back that it hit the cap (it will say so
explicitly), later forks in the same wave will mostly come back empty too.
Keep an eye on this and tell the user when it happens rather than quietly
returning thin batches — they can raise
`CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` if they want to keep going.

## 4. Add the known-ATS batch (active immediately)

```
POST /admin/crawl-sources  {"name": "...", "board_url": "..."}
```

This always creates the row as `active` — it 422s if `board_url` doesn't
resolve via `detect_ats_source`, 409s if that `board_url` is already
registered (just means it's a dupe, skip it). A plain Python script looping
over `(name, url)` pairs with `urllib.request` is enough; no need for
`requests`. Report each HTTP status back rather than assuming success.

**Gotcha — SuccessFactors (and any embedded-match-only adapter):** some
adapters (SuccessFactors is the current example) only match via
`detect_embedded_ats_source`, which the admin `POST` endpoint never calls —
only `detect_ats_source` (static URL-shape matching). Submitting the bare
careers-portal root through `/admin/crawl-sources` 422s even though the
platform is supported. Fix: submit one real job-posting URL from that board
through step 5's flow instead (`POST /jobs`) — the embedded-match path there
will resolve it straight to `active`.

**Gotcha — Workday/Eightfold content verification:** these render via a
client-side JS SPA or a POST-only JSON API, so `WebFetch` on the board root
typically comes back empty — this is expected and matches every other
Workday board already active in the system (e.g. Phreesia). "Verified" for
these means a real individual job-requisition URL was found via search, not
a successful raw page fetch. Trust the URL shape once that's confirmed;
don't drop a Workday/Eightfold candidate just because `WebFetch` renders
nothing.

**Gotcha — name collisions:** disambiguate company names that collide with
something already in the system (e.g. "Circle" the USDC issuer vs.
Circle.so the community-software company; "Root Insurance" vs. an unrelated
"Root" already covering something else) — pick a name that won't confuse
the admin list later.

**Gotcha — zero live postings:** if a fork found a valid board with
currently zero open postings, don't add it — nothing to crawl yet. Note it
as skipped rather than silently dropping it, in case it's worth a retry
later.

## 5. Register the unsupported-platform batch (lands as pending)

The admin `POST` endpoint can only create `active` rows — it can't create a
`pending` placeholder directly. To get a genuinely-unsupported platform into
the queue, submit one real job-posting URL through the normal public
submission flow instead, using the same token (it's also a valid user
bearer token):

```
POST /jobs  {"url": "https://<real job posting url>"}
```

This calls `register_discovered_board` under the hood (see
`app/services/crawl_sources.py`) — same path a real user hitting "submit
job" would trigger. A URL matching an already-supported platform (including
via the embedded-fallback path, like Clinch or SuccessFactors) resolves
straight to `active`; a genuinely new platform lands as `pending`, keyed by
domain, `board_url` stored verbatim as `https://{domain}`. **Always use a
real, specific job-posting URL** you've confirmed is live, not a guessed
one — never fabricate or template a board_url; the literal submitted URL is
the source of truth here, same as for any new-adapter work
(`docs/adapter-playbook.md`).

Note: multiple companies hosted on the same generic platform domain (e.g.
several different companies all on `jobs.smartrecruiters.com`) collapse into
a **single shared pending row** keyed by that domain — this is correct
behavior, not a bug. Once someone implements the adapter for that platform,
it covers every company on it generically (matching URL shape), so one
pending row per platform-domain is exactly right, not one per company.

## 6. Report and checkpoint

After each sector (or each wave of parallel sector forks), report back to
the user: what got added (by sector, active vs. pending), what got skipped
and why, any prod errors hit along the way (a real 500, not just a 422/409
— those are worth flagging separately since they may be an actual bug, not
just "platform not supported"), and the running total (`GET
/admin/crawl-sources` status counts). Don't silently keep expanding scope
across many sector waves without checking in — this is real prod data
growth, not a one-shot reversible action.
