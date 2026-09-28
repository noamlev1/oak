# 07 - Task Test Harness

Workflow id `nyht2byorvoxFX2u` · 12 nodes · error workflow is 99 · read from the deployed workflow on 2026-09-28 (version `430b5396`, which added the two n8n-variable switches).

## In one breath

07 is a one-click harness that proves the pipeline works instead of asking anyone to trust it. Nothing calls it and nothing depends on it: you hit Execute, it POSTs booth scans to 01's live production webhook exactly as a badge scanner would, waits for the asynchronous tail to finish, then reads what was actually stored and returns one PASS/FAIL row per scan plus an overall verdict.

## Trigger, input, output

| | |
|---|---|
| Trigger | `Run Task Test Suite` (Manual Trigger). Run by hand only. |
| Input | Either scans pasted into `Paste JSON Here` (`{"reset": true, "scans": [...]}`, plus optional `gap_seconds`, `settle_seconds`), or, when `scans` is empty, rows of the `oak_test_payloads` Data Table chosen by the n8n variable `OAK_TEST_SUITE` (unset: the `enabled` rows). Expectations come from each row's `expect_json`, matched by `scan_id`, or an inline `expect`. |
| Output | One item from `Score Test Run`: `verdict`, `total`, `passed`, `failed`, `scans_came_from`, `slack_alerts_delivered`, `slack_failures`, a one-line `headline` per scan, and a `results` array with every check. |
| Side effects | Real ones. Deletes this test event's rows from `oak_deliveries` and `oak_encounters` (when reset is on), then every scan runs the full pipeline: HubSpot writes, Brave and Gemini calls, Slack posts. |

## Step by step

### Trigger

**1. `Run Task Test Suite`** (Manual Trigger) - one click, no Postman, no curl. A demo that needs a second tool is a demo that breaks.

### Group: 1. Pick the scans to run

**2. `Paste JSON Here`** (Set, raw JSON) - defaults to `{"reset": true, "scans": []}`. Put one or more scan objects in `scans` for a one-off run; they take precedence over the table. Only `reset`, `gap_seconds` and `settle_seconds` are harness controls, and they sit outside `scans`, so a scan reaches the webhook as the task payload verbatim.

**3. `Load Test Payloads`** (Data Table get, `oak_test_payloads`, every row with a non-empty `payload_json`) - loads all rows, not just enabled ones: `OAK_TEST_SUITE` decides which rows supply the scans, and every row supplies expectations by `scan_id`. Tests live in data, so adding a case is a row, not a code edit. Soft-fails and always outputs.

The table holds 20 cases in suites `task` (the four leads), `returning-visitor`, `edge-cases` (personal email, invalid scan, ambiguous title, 500-999 band, no email), `caching`, `security` (prompt injection), `personas`, `triggers` (audit deadline, breach, Saviynt renewal), `competitors` and `sources`. Check before a demo: at the time of writing the four `task` rows are `enabled: false` and rows 5 to 8 (Rachel's second scan, personal email, invalid scan, ambiguous title) are enabled, so a default run does not run the four task leads, even though the canvas sticky says it does. Rachel's second scan is also meant to run with Lead 1 and `reset: false`. The clean way to demo the brief is to set `OAK_TEST_SUITE=task`, which runs exactly the four leads whatever their `enabled` flag.

**4. `Build Test Plan`** (Code) - turns the input into one plan item per scan.
- **Config block at the top.** Two settings come from n8n variables, so neither needs a code edit:
```js
const WEBHOOK_URL    = String($vars.OAK_BOOTH_WEBHOOK_URL || '').trim() || 'https://oak-noam.app.n8n.cloud/webhook/oak-event/scan';
const RUN_SUITE      = String($vars.OAK_TEST_SUITE || '').trim() || 'enabled';   // 'enabled' | 'all' | a suite name
const GAP_MS         = 15000;
const SETTLE_SECONDS = 90;
const TEST_EVENT_ID  = 'oak-live-event-2026';
const RESET_ON_MANUAL_RUN = true;
```
- `OAK_BOOTH_WEBHOOK_URL` points the harness at another instance (staging, a second tenant); unset, it targets this instance's booth webhook.
- **Source priority:** pasted scans first. Otherwise `RUN_SUITE` picks table rows (sorted by `sort_order`):
```js
if (RUN_SUITE === 'enabled') return p.row.enabled === true;
if (RUN_SUITE === 'all') return true;
return String(p.row.suite || 'default') === RUN_SUITE;
```
  - unset or `enabled`: the rows ticked `enabled` (the default run)
  - `all`: every row, ignoring `enabled`
  - any other value, such as `task`, `edge-cases`, `triggers`, `competitors`: that suite, ignoring `enabled`. A suite name no row carries **throws** ("OAK_TEST_SUITE is "x" but no oak_test_payloads row has that suite. Use enabled, all, or one of: ...") instead of quietly falling back to the four task leads and reporting green.
  - Only when `enabled` (or `all`) selects nothing does it use the built-in `FALLBACK` of the four task leads (Rachel, Hiroshi, David, Sarah), so an empty table never produces an empty run.
  - The result's `scans_came_from` says which source ran, for example `oak_test_payloads, OAK_TEST_SUITE=task`.
- **Validation that throws** (a hard stop is better than a misleading run): invalid `payload_json` or `expect_json`, a payload that is not an object, a missing `scan_id`, and two scans in one run sharing a `scan_id`:
```js
if (seen[r.payload.scan_id]) throw new Error('Two scans in this run share scan_id "' + r.payload.scan_id + '". The replay guard would drop the second one.');
```
  01 blocks `scan_id` replays, so a duplicate would be dropped silently and the test would halve itself.
- Strips a test-only `expect` key from pasted scans before they are sent.
- Emits per scan: `payload`, `expect`, `label`, `suite`, `notes`, `webhook_url`, `gap_ms`, `settle_seconds`, `test_event_id`, and `reset_filter`, which is `oak-live-event-2026` when reset is on and `__no_reset__` when it is off, so the delete nodes match nothing.

### Group: 2. Reset, then send every scan

**5. `Clear Test Event Scans`** (Data Table delete on `oak_deliveries` where `event_id = reset_filter`, execute once) - removes this test event from the replay ledger so the same `scan_id`s can be sent again.

**6. `Clear Test Event Encounters`** (Data Table delete on `oak_encounters`, same filter, execute once) - removes returning-visitor state so every scan starts as a first scan, not a threaded repeat. `reset: false` is how you deliberately test a returning visitor.

**7. `Resume Test Plan`** (Code) - the delete nodes output deleted row ids, not test items, so this reads the plan back from `Build Test Plan`.

**8. `POST Booth Scan`** (HTTP POST to `webhook_url`) - sends each payload with the `x-oak-webhook-secret` header from `$vars.OAK_WEBHOOK_SECRET`, which 01 verifies. Batching is 1 item at a time with `gap_ms` (15 seconds) between them, so scans do not race each other and a second scan of the same company can hit the cache the first one wrote. `fullResponse` and `neverError` keep a 400 or 401 as data to score, 30 second timeout, soft-fail.

### Group: 3. Wait, read and score

**9. `Wait for Pipelines to Finish`** (Wait, `settle_seconds`, default 90, execute once) - 01 answers the scanner with a provisional 202 in about two seconds and runs enrichment, AI, the final gate, HubSpot and Slack afterwards. Scoring before that tail finishes would score the wrong thing.

**10. `Read Final Decisions`** (Data Table get on `oak_deliveries` where `event_id = test_event_id`, return all) - reads the stored decision, not the HTTP reply, because the reply is sent before the final deterministic gate runs.

**11. `Score Test Run`** (Code) - one row per scan. For a scan expected to be accepted, the checks are, in order:
1. the webhook accepted it (`body.accepted === true`)
2. a decision row exists in `oak_deliveries`
3. the ledger reached `status: complete` inside the settle wait (separates "still running" from "died after the 202")
4. when the stored decision names a channel, Slack actually delivered: the receipt from 03 shows `delivered_count > 0` and `failed_count === 0`
```js
pass: slackReceipt ? (slackDelivered > 0 && slackFailed === 0) : false,
```
5. then, only if expected: `classification`, `persona`, and channel (`raw.routing.channel`)

For a case with `{"accepted": false}` the first two invert: the webhook must reject it and no row may exist. It is the only way a case passes by nothing happening. Each result also reports `answered_to_scanner` vs `stored_after_pipeline` and `changed_after_the_202`, score, rule, headcount with source and confidence, HubSpot contact id, `enrichment_cache_hit`, and whether it was threaded. `FAIL` if any check fails.

## Decisions worth defending

1. **Score from stored state, not the HTTP reply.** The 202 is provisional by design; `changed_after_the_202` shows where the final gate or enrichment moved a lead.
2. **Slack delivery is an assertion, not a hope.** 03 soft-fails Slack, which is only safe because this harness fails when nothing was delivered, for example a channel the app never joined.
3. **Tests live in a Data Table.** Add, edit or toggle a case in the n8n UI; expectations travel with the payload.
4. **Hit the real production webhook.** It tests auth, replay protection, the async tail and the real integrations, exactly as a scanner would. The target is an n8n variable, so the same harness can test another instance.
5. **Suite selection is a variable, and a wrong value fails.** There is no bulk toggle for `enabled` in the Data Table UI, so `OAK_TEST_SUITE` is the switch; a mistyped suite throws rather than falling back and reporting green.
6. **Serial with a 15 second gap and a 90 second settle.** Predictable, cache-realistic, and no race between scans of the same company.
7. **Fail loudly on bad test data.** Duplicate `scan_id`s or malformed JSON stop the run before anything is sent.

## Likely interview questions

**How do you know the system works?** I run 07. It replays the saved cases through the live webhook, waits for the async work, then checks from the stored rows that each scan was accepted, completed, delivered to Slack, and landed on the expected classification, persona and channel. The four task leads are expected at Rachel `tier_1`/`#booth-hot`, Hiroshi `matched`/`#booth-matched`, David `out_of_scope`/`#booth-out-of-scope`, Sarah `competitor_intel`/`#competitive-intel`.

**Why not assert on the webhook response?** 01 replies in about two seconds with a provisional classification and keeps working for up to a minute: enrichment, AI, the final gate, HubSpot and Slack. The truth is the `oak_deliveries` row after that. The harness reports both and flags when they differ.

**How do you add a test, or run a different set?** Add a row to `oak_test_payloads` with `payload_json`, `expect_json`, a `suite`, a `label` and `enabled`. No workflow edit. To choose what runs, set the n8n variable `OAK_TEST_SUITE`: unset runs the enabled rows, `all` runs all 20, a suite name like `triggers` runs just that suite, and a typo fails loudly instead of silently running something else. For a one-off, paste the scan into `Paste JSON Here`.

**How do you test a returning visitor?** Run the first scan, then run the second with `reset: false`, so the encounter row survives and 01 treats the badge as a repeat. The saved row "Rachel, second scan - new notes" is built for that, and the result shows `repeat_scan`, `scan_number` and `slack_threaded`.

**What is the reset, and is it safe?** It deletes `oak_deliveries` and `oak_encounters` rows whose `event_id` is `oak-live-event-2026`, so the same `scan_id`s can be replayed past the duplicate guard. Be precise here: that is also the default `event_id` 01 assigns to any real scan that arrives without one, so on a live event you would give the harness its own event id or turn reset off. Nothing outside that event id is touched.

**Why a 15 second gap and 90 second settle?** The gap keeps scans serial so they do not race and so a second person from the same company genuinely hits the enrichment cache. The settle covers 01's asynchronous tail. Both are overridable per run with `gap_seconds` and `settle_seconds`.
