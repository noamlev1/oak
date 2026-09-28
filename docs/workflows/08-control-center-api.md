# 08 - Control Center API

Workflow: `Oak GTM - 08 Control Center API` (id `F1WVtZUcC3Y54acX`), project `xYAQqquu3BTBSjcQ`.
Error workflow: `99 Failure Handler` (`N4JW7CO6qjdLi6mq`).

## What it is

08 is the live backend for the Oak control center app (`app/oak-control-center.html`). Before 08 the app could
only run on an offline snapshot baked into the page. 08 serves the same data live from the Oak Data Tables and
the n8n API, and it is the only path through which the app can change anything: policy drafts, policy
activation, competitor records, and a guarded live send to the booth webhook.

Every response has the same keys the app's offline mock (`offlineApi()` and `window.OAK_OFFLINE_STATE`) returns,
so the UI renders the same live or offline. Extra keys are additive.

08 reads the Data Tables by name (`oak_policies`, `oak_competitors`, `oak_deliveries`, `oak_encounters`,
`oak_test_payloads`), never by id.

## Base URL

`https://oak-noam.app.n8n.cloud/webhook/oak-api`

Every route is a static webhook path. Ids travel as query parameters, because n8n puts the webhook node id into
the URL of any path that contains `:param`.

## Routes

| Method | Path | Auth | What it returns |
|---|---|---|---|
| GET | `/health` | open | `{ ok, mode: "live", gateway_configured, scanner_configured, admin_configured, n8n_api_configured, slack_actions_enabled, policy_version, active_policy_rows, counts, last_scan_at, api_base, generated_at }` |
| GET | `/dashboard?event_id=` | open | `{ mode, generated_at, event_ids, selected_event_id, metrics, classification_counts, policy_versions, recent, workflow_health, alerts }` |
| GET | `/leads?search=&tier=&event_id=` | open | `{ items, count, total }`, newest first |
| GET | `/leads/item?scan_id=` | open | one lead, plus `found`, `result`, `decision` (full `raw_json`), `delivery` (`slack_json`), `encounter` |
| GET | `/policy` | open | `{ version, status, activated_by, activated_at, document, versions, projected_keys, warnings }` |
| POST | `/policy/drafts` | admin | body `{ version, document }`; returns `{ version, status: "draft", validation, regression, ... }` (201) |
| POST | `/policy/activate?version=` | admin | body `{ confirm: "ACTIVATE <version>" }`; returns `{ version, status: "active", superseded, ... }` |
| GET | `/competitors` | open | `{ items }` |
| POST | `/competitors` | admin | body `{ name, category, domains, aliases, status, active }`; returns `{ item, warnings }` (201) |
| PUT, PATCH | `/competitors/item?id=` | admin | PUT replaces the editable fields, PATCH merges; returns `{ item, warnings }` |
| DELETE | `/competitors/item?id=` | admin | returns `{ deleted: true, id, name }` |
| GET | `/workflows` | open | `{ items, source, api, generated_at }`: the ten Oak workflows |
| POST | `/payloads/validate` | open | `{ ok, mode: "validate", normalized_payload, preview, side_effects_executed: false, warnings }` |
| POST | `/payloads/dry-run` | open | same shape, `mode: "dry-run"` |
| POST | `/payloads/send` | admin | body `{ payload, confirm: "SEND LIVE" }`; returns 01's answer plus `status_url` |
| GET | `/payloads/status?scan_id=` | open | `{ found, scan_id, status, result }` (extra route, see below) |

Errors are non-2xx with `{ "error": "...", "details": ... }`.

### Leads

A lead is built from one `oak_deliveries` row (ledger fields, `raw_json`, `slack_json`) joined to its
`oak_encounters` row by `event_person_key`.

- `classification` is the current class: a rep's override from 06 (`override_classification`) wins over the
  ledger value. `ledger_classification` and `rep_override` show both.
- `claimed_by` and `booth_status` come from the encounter row that 06 writes.
- `completed_at` is the Slack receipt time; `latency_ms` is intake to receipt.
- `evidence` merges competitor identity, urgency quotes, the resolved headcount, the resolved industry, person
  evidence and homepage quotes. Homepage quotes carry `confidence: null` because 04 does not score them.

### Dashboard

Aggregates over the selected event: scan count, completion rate, one count per class (all five always present),
claimed count, median intake-to-Slack latency, enrichment complete (`complete` or `cache_hit`), class and policy
mix, the eight newest scans, workflow health and alerts. Alerts are computed, not canned: missing API key or
variables, inactive workflows, failures in the last 24 hours, scans stuck short of `complete` for more than ten
minutes, rejected Slack deliveries, and unclaimed Tier 1 leads.

### Workflows

The ten Oak workflows (00, 01, 02A, 02, 03, 04, 05, 06, 07, 99). With `OAK_N8N_API_KEY` set, 08 reads the n8n
public API: `state` is `active`, `inactive`, `manual` (00 is draft-only by design), `draft-drift` or `missing`,
and `health` is `healthy`, `warning` (a failure in the last 24 hours, or an unpublished draft) or `critical`
(inactive or missing). Without the key, `state` and `health` are `unknown` and `source` is `static`.
`last_activity_at` is filled either way: from the last execution when the API answers, otherwise from the Data
Table timestamps each workflow leaves behind (01 intake row, 02 and 02A CRM sync time, 03 Slack receipt,
04 enrichment time, 05 reasoning time, 06 last button press).

### Policy

`document` is the live active document plus a few projected keys the app edits but the live document stores
elsewhere or not at all: `event_id`, `enrichment.cache_ttl_days` (stored as `company_cache_ttl_days`),
`enrichment.employee_source_precedence` (derived from `employee_confidence`), `feature_flags`
(`public_search_enabled` is `enrichment.search_enabled`; `slack_actions_enabled` mirrors the n8n variable
`OAK_SLACK_ACTIONS_ENABLED`), `owner_directory`, and `notifications.<class>.mention` / `quiet`. They are listed
in `projected_keys`, and a draft maps them back before anything is stored, so a stored document keeps the exact
key set 01, 03 and 04 read.

Draft (`POST /policy/drafts`):

1. The version name must be new and match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
2. The document must be a JSON object (a string is parsed; broken JSON is refused with 422).
3. Validation errors refuse the draft with 422: review band not below the qualified band, non-numeric or negative
   scoring, components above `scoring.max`, an empty or malformed Slack channel for any class, no persona with
   title patterns, confidence values outside 0 to 1, a document over 200 KB. Warnings do not block.
4. Regression: every `oak_test_payloads` row with an expected class is replayed through 01's intake rules under
   the draft and under the active policy. A case passes when the draft matches the expectation; for the
   stateful cases whose expectation depends on a previous scan, it passes when the draft matches the active
   policy.
5. The row is inserted with `status: draft`. 01 only ever reads the `active` row, so a draft changes nothing.

Activate (`POST /policy/activate?version=`): the row must be `draft` or `superseded`, the body must carry the exact
phrase `ACTIVATE <version>`, and the stored document must still validate. The new version is set `active` first,
then every other active row becomes `superseded`, so 01 never finds zero active rows mid-switch. Superseded rows
stay in the table, so a rollback is another activation.

### Validate and dry-run

Both are side-effect free. They run 01's own `Normalize Scan` and `Evaluate ICP Policy` code, ported verbatim into
08 as pure functions, against the live active policy and the active competitor rows. The preview is therefore the
decision 01 makes at intake, before HubSpot, enrichment, AI and the final gate; a CRM or website headcount, or a
returning-visitor history, can still move it. `preview` carries the offline keys (`classification`,
`would_be_classification`, `company_size_band`, `needs_headcount_verification`, `score`, `mutations`) plus the
persona, rule summary, score breakdown and reasons. A payload 01 would reject (no `scan_id`, or no identity at
all) gets 422 with the validation errors.

Warnings point out what the offline preview hides: 01 does not read `employee_count_observed` (it reads a stated
headcount from `notes_from_booth`), a `scan_id` that already exists would be answered `duplicate_ignored`, and
live send only accepts `oak-ui-test-*` ids.

### Live send

`POST /payloads/send` is admin only and refuses unless `confirm` is `SEND LIVE`, the `scan_id` starts with
`oak-ui-test-`, and 01 would accept the payload. 08 then POSTs the payload to
`https://oak-noam.app.n8n.cloud/webhook/oak-event/scan` with `x-oak-webhook-secret` set server side from
`$vars.OAK_WEBHOOK_SECRET`; the browser never sees the secret. 08 relays 01's answer (a 202 means accepted, not
complete) and adds `status_url: /api/leads/<scan_id>`, which the app polls through the lead route until
`status` is `complete`. `status_url_absolute` and `payload_status_url` give the same lookup as absolute URLs.

## Auth

- Reads are open.
- Every POST, PUT, PATCH and DELETE except validate and dry-run needs header `x-oak-admin-token` equal to the n8n
  variable `OAK_CONTROL_ADMIN_TOKEN`, compared in constant time.
- Missing or wrong header: 401 `{ error: "Missing or incorrect admin key (x-oak-admin-token)." }`.
- Variable unset: every admin call is refused with 503 (fails closed). No admin branch runs before this check.

## CORS

- Every Webhook node sets Allowed Origins to `*`.
- n8n answers the OPTIONS preflight itself for every registered path. It echoes the request `Origin` (so a page
  opened from a file, `Origin: null`, gets `Access-Control-Allow-Origin: null`), echoes the requested headers
  (so `content-type, x-oak-admin-token` is allowed), and lists the methods registered on that path plus
  `OPTIONS`. A Webhook node cannot listen for OPTIONS, so the preflight headers are n8n's, not 08's.
- Every real response goes through a Respond to Webhook node that sets `Access-Control-Allow-Origin: *`,
  `Access-Control-Allow-Headers: content-type, x-oak-admin-token`,
  `Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS` and `Cache-Control: no-store`.

## Variables

| Variable | Required | Used for |
|---|---|---|
| `OAK_CONTROL_ADMIN_TOKEN` | yes, for any admin change | the admin key the app sends as `x-oak-admin-token`. Unset means every admin route answers 503. |
| `OAK_N8N_API_KEY` | optional | an n8n API key (Settings, n8n API). Adds live active state, last run and 24h failures per workflow. Unset means `unknown`. |
| `OAK_WEBHOOK_SECRET` | existing | added to live sends. 08 only reads it. |
| `OAK_SLACK_ACTIONS_ENABLED` | existing | mirrored read-only into `feature_flags.slack_actions_enabled`. |

## Canvas

Two lanes. The read lane is one capture node, four table loads (`executeOnce`, so a multi-row table never
multiplies the next load), an optional pair of n8n API calls, one builder and one responder. The admin lane is
the token check, a switch per action, and one short branch each for draft, activate, competitors and live send,
all ending in one responder. Every write node continues on error so the caller always gets a JSON answer.

## Known limits

- Reads load whole tables. That is fine at event scale (hundreds of scans) and would need filtered reads for tens
  of thousands.
- The dry-run is the intake decision, not the final one (see above).
- Homepage evidence has no confidence score; the app shows it as 0% unless it treats `null` as unscored.
