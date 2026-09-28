# Oak GTM - test case catalogue

Every row of the `oak_test_payloads` Data Table (`G6sBwk6wTFKmidiZ`), grouped by suite. Workflow 07 (Task Test Harness) POSTs each payload to the live booth webhook exactly as a scanner would, waits for the pipeline to settle, then scores the stored decision in `oak_deliveries`.

## How to run

**One suite or everything.** Set the n8n variable `OAK_TEST_SUITE` (Settings > Variables) and run workflow 07 with **Execute workflow**:

| `OAK_TEST_SUITE` | What runs |
|---|---|
| `enabled` (or unset) | Only rows with `enabled = true`, the old behaviour |
| a suite name, e.g. `boundaries` | Every row in that suite, whatever its `enabled` flag |
| `all` | All 63 rows |

> **Check first:** the `OAK_TEST_SUITE` switch is being added to 07's `Build Test Plan` node by another agent. Open that node and confirm it reads `$vars.OAK_TEST_SUITE` before relying on it. Until it exists, 07 runs only rows with `enabled = true` (today that is rows 5 to 8, not the four task leads), so either flip `enabled` on the rows you want or paste payloads into `scans` in the `Paste JSON Here` node, which takes precedence over the table.

**Harness controls** live in `Paste JSON Here`, outside `scans`: `reset` (default `true`: deletes every `oak_deliveries` and `oak_encounters` row with event_id `oak-live-event-2026` first, so scan_ids can be replayed), `gap_seconds` (default 15, between POSTs) and `settle_seconds` (default 90, after the last POST).

**Suite-specific instructions**

- `returning-visitor`: use `gap_seconds: 90` so the first card exists before the second scan arrives and the reply threads under it. The classifications are asserted either way. Row 5 (Rachel) depends on Lead 1 running first in the same pass.
- `replay`: run once with `reset: true`, then again with `reset: false`. The second run's `POST Booth Scan` output must show `status: duplicate_ignored`, and no second card may appear in #booth-matched. 07 cannot assert this itself (see Harness limits).
- `caching`: run both rows in one pass with at least a 30 second gap.
- `all` takes roughly 16 minutes of POSTs at the default 15 second gap plus the settle wait. Suites are faster and fail more legibly.

**What a PASS means for an accepted scan:** the webhook accepted it, an `oak_deliveries` row exists, that row reached `status: complete`, Slack delivered the card with no failures, and then each key present in `expect_json` matches: `classification`, `persona` and `channel`. For `{"accepted": false}` the webhook must reject the scan and no row may exist. No other `expect_json` key is checked.

**Test data.** Rows 21 to 63 use scan_ids `evt_9921` to `evt_9963`, the default event_id (none is sent, so 01 applies `oak-live-event-2026` and the reset clears them), invented people, and invented domains ending in `-oakqa.com` (or a `gmail.com` address) so enrichment finds nothing and outcomes stay deterministic. The two exceptions are deliberate: `delinea.com` (row 51) because a competitor-domain match needs a real listed domain, and competitor names in rows 52 and 54. Every accepted row states its headcount in the notes, so the size band never depends on web search, except row 27 (empty notes by design) and competitor rows 51 and 52, where size cannot change a confirmed-competitor verdict. All 43 new rows are `enabled = false`.

## Suite: `task` (4)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 1 | `evt_9901` | Lead 1 - The Ideal Target | CISO plus a SailPoint renewal inside six months is Tier 1 even with headcount unverified (70/100). | `tier_1` / persona `ciso` / #booth-hot |
| 2 | `evt_9902` | Lead 2 - The Technical Practitioner | An IAM title with pain signals but no trigger is matched; pain is evidence, not score (30/100). | `matched` / persona `iam` / #booth-matched |
| 3 | `evt_9903` | Lead 3 - The Bad Fit (Too Small) | 80 employees and SaaS-only each disqualify on their own; the AI step is skipped. | `out_of_scope` / persona `other` / #booth-out-of-scope |
| 4 | `evt_9904` | Lead 4 - The Competitor / Intel Scan | The veza.com domain in oak_competitors makes this competitor intel; no sales-persona AI. | `competitor_intel` / persona `other` / #competitive-intel |

## Suite: `validation` (11)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 21 | `evt_9921` | Rejected - no email, no name, no company | Title and notes alone are not an identity: no email, name or company is rejected. | Rejected: HTTP 400 invalid, no oak_deliveries row |
| 22 | `evt_9922` | Rejected - identity fields are only whitespace | Whitespace-only identity fields are trimmed to empty and rejected. | Rejected: HTTP 400 invalid, no oak_deliveries row |
| 23 | `evt_9923` | Accepted - no email and no name, company only | A company with no email and no name is accepted and classified; only the contact sync is skipped. | `matched` / persona `ciso` / #booth-matched |
| 24 | `evt_9924` | Malformed email is dropped, scan still processed | A malformed email is blanked, not fatal, when a name and company exist. | `matched` / persona `ciso` / #booth-matched |
| 25 | `evt_9925` | Missing job_title field | An absent job_title is not fatal; no persona and no relevance signal means out of scope with no AI call. | `out_of_scope` / persona `other` / #booth-out-of-scope |
| 26 | `evt_9926` | Missing company_name - inferred from the email domain | An absent company_name is inferred from the corporate email domain. | `matched` / persona `ciso` / #booth-matched |
| 27 | `evt_9927` | Empty booth notes | Empty notes are not fatal; an IAM title with no trigger is matched with headcount to confirm. | `matched` / persona `iam` / #booth-matched |
| 28 | `evt_9928` | Wrong field types are coerced, not crashed | Wrong types (numeric email, null last name, title as a list) are coerced by String(), not crashed on. | `matched` / persona `ciso` / #booth-matched |
| 29 | `evt_9929` | Extremely long booth notes (about 4,500 characters) | A renewal at the end of 4,500 characters of notes still fires Tier 1 and the card still posts. | `tier_1` / persona `compliance` / #booth-hot |
| 30 | `evt_9930` | Right-to-left unicode name | A Hebrew right-to-left name passes validation, HubSpot and Slack unchanged. | `matched` / persona `ciso` / #booth-matched |
| 31 | `evt_9931` | Leading and trailing whitespace, mixed-case email | Padded fields are trimmed and the email lowercased before any rule runs. | `matched` / persona `iam` / #booth-matched |

## Suite: `edge-cases` (7)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 6 | `evt_9906` | Personal email, no company | Personal email with no company: no domain inferred, company search skipped, work-email warning on the card. | persona `iam` |
| 7 | `evt_9907` | Invalid - no identity at all | No usable identity at all is rejected with 400 invalid and nothing is stored. | Rejected: HTTP 400 invalid, no oak_deliveries row |
| 8 | `evt_9908` | Ambiguous title - AI resolves persona | An unmatched title goes to review as ambiguous and Gemini proposes the persona. | Accepted, pipeline completes, Slack delivered (no verdict asserted) |
| 11 | `evt_9911` | 500-999 employees - the review band | 750 employees sits in the 500 to 999 band, so a matched persona still goes to a human. | `needs_review` / persona `iam` / #booth-review |
| 19 | `evt_9919` | No email - ledger must still complete | No email: the HubSpot contact write is skipped but the ledger still reaches complete. | `matched` / persona `ciso` / #booth-matched |
| 32 | `evt_9932` | Personal email with a real company name | A Gmail address is never the company domain, but it is not a disqualifier. | `matched` / persona `ciso` / #booth-matched |
| 33 | `evt_9933` | Email domain differs from the company name | Email domain and badge company can disagree without changing the decision. | `matched` / persona `ciso` / #booth-matched |

## Suite: `personas` (7)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 13 | `evt_9913` | Compliance persona - no trigger | SOX Manager is the compliance persona; no trigger means matched, not Tier 1. | `matched` / persona `compliance` / #booth-matched |
| 34 | `evt_9934` | CISO by abbreviation | The bare abbreviation CISO resolves the CISO persona by rule. | `matched` / persona `ciso` / #booth-matched |
| 35 | `evt_9935` | CISO by Head of Information Security | Head of Information Security is a CISO title pattern. | `matched` / persona `ciso` / #booth-matched |
| 36 | `evt_9936` | IAM by IGA Architect | Senior IGA Architect resolves IAM by rule despite the seniority prefix. | `matched` / persona `iam` / #booth-matched |
| 37 | `evt_9937` | Compliance by GRC Manager | GRC Manager is a compliance title pattern. | `matched` / persona `compliance` / #booth-matched |
| 38 | `evt_9938` | Ambiguous title with a real trigger - AI decides | Ambiguous title with a real renewal trigger: Gemini decides, so only acceptance and completion are asserted. | Accepted, pipeline completes, Slack delivered (no verdict asserted) |
| 39 | `evt_9939` | Senior title, wrong function - CIO | A senior but wrong-function title (CIO) earns nothing and is out of scope with no AI call. | `out_of_scope` / persona `other` / #booth-out-of-scope |

## Suite: `boundaries` (6)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 40 | `evt_9940` | Exactly 499 employees | 499 employees is below the 500 floor: out of scope even with an IAM persona. | `out_of_scope` / persona `iam` / #booth-out-of-scope |
| 41 | `evt_9941` | Exactly 500 employees | 500 employees is the first value of the review band. | `needs_review` / persona `iam` / #booth-review |
| 42 | `evt_9942` | Exactly 999 employees | 999 employees is the last value of the review band. | `needs_review` / persona `iam` / #booth-review |
| 43 | `evt_9943` | Exactly 1,000 employees | 1,000 employees meets the qualified threshold (comma format parsed). | `matched` / persona `iam` / #booth-matched |
| 44 | `evt_9944` | Disqualifier - SaaS-only | The saas-only disqualifier beats a CISO at a 5,000-person company. | `out_of_scope` / persona `ciso` / #booth-out-of-scope |
| 45 | `evt_9945` | Disqualifier - single-cloud beats a Tier 1 renewal | The single-cloud disqualifier beats a CISO with a SailPoint renewal inside six months. | `out_of_scope` / persona `ciso` / #booth-out-of-scope |

## Suite: `triggers` (8)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 14 | `evt_9914` | Tier 1 by audit deadline | The audit-deadline branch of the Tier 1 trigger, plus 14,000 stated employees earning size points. | `tier_1` / persona `compliance` / #booth-hot |
| 15 | `evt_9915` | Tier 1 by identity breach | The identity-breach branch of the Tier 1 trigger. | `tier_1` / persona `ciso` / #booth-hot |
| 16 | `evt_9916` | Tier 1 by Saviynt renewal (not SailPoint) | The renewal trigger covers any active incumbent IGA tool (Saviynt), not only SailPoint. | `tier_1` / persona `iam` / #booth-hot |
| 46 | `evt_9946` | Renewal at 6 months - inside the window | A renewal at exactly 6 months is inside the window (less than or equal). | `tier_1` / persona `iam` / #booth-hot |
| 47 | `evt_9947` | Renewal at 7 months - outside the window | A renewal at 7 months is outside the window, so the same lead is only matched. | `matched` / persona `iam` / #booth-matched |
| 48 | `evt_9948` | Renewal of an unrecognised tool | A renewal of an unnamed tool is logged as unverified and does not trigger Tier 1. | `matched` / persona `ciso` / #booth-matched |
| 49 | `evt_9949` | Breach reported but no persona | A breach with no target persona and no relevance signal does not make a lead. | `out_of_scope` / persona `other` / #booth-out-of-scope |
| 50 | `evt_9950` | Under 500 employees with a breach | Under 500 employees beats a CISO reporting an identity breach. | `out_of_scope` / persona `ciso` / #booth-out-of-scope |

## Suite: `competitors` (6)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 17 | `evt_9917` | Unlisted competitor caught by company name | A company name with an identity-vendor token is flagged as a possible competitor. **Likely FAIL against deployed logic: the final deterministic gate in 01 turns any disqualified_under_500 lead into out_of_scope and does not exempt competitor_status possible, so 60 people ends in #booth-out-of-scope.** | `needs_review` / #booth-review |
| 18 | `evt_9918` | Small identity vendor - homepage language only | A small identity vendor is still homepage-checked for competitor language; outcome depends on their site. | Accepted, pipeline completes, Slack delivered (no verdict asserted) |
| 51 | `evt_9951` | Competitor by email domain, unrelated company name | A competitor email domain wins even when the badge names an unrelated company. | `competitor_intel` / persona `other` / #competitive-intel |
| 52 | `evt_9952` | Competitor by alias in company name, personal email | A whole-word competitor alias in the company name wins with a personal email and no domain. | `competitor_intel` / persona `other` / #competitive-intel |
| 53 | `evt_9953` | Possible-competitor token outranks a Tier 1 trigger | A possible-competitor token in the company name beats a CISO with a live renewal. | `needs_review` / persona `ciso` / #booth-review |
| 54 | `evt_9954` | Competitor employee whose notes mention a renewal | A confirmed competitor wins over a CISO title plus a Tier 1 renewal in the notes. | `competitor_intel` / #competitive-intel |

## Suite: `security` (5)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 12 | `evt_9912` | Prompt injection in booth notes | Instruction text in the notes cannot change routing; the card flags suspected injection. | `matched` / persona `ciso` / #booth-matched |
| 55 | `evt_9955` | Injection cannot promote an irrelevant title | An injection in the notes cannot promote an irrelevant title; the AI is never called. | `out_of_scope` / persona `other` / #booth-out-of-scope |
| 56 | `evt_9956` | Injection text inside the job title | Injection text inside the job title cannot override the rule persona or add a trigger. | `matched` / persona `ciso` / #booth-matched |
| 57 | `evt_9957` | Injection with a genuine trigger - rules still stand | An injection with a genuine renewal: Gemini is skipped and the rules still give Tier 1. | `tier_1` / persona `iam` / #booth-hot |
| 58 | `evt_9958` | HTML and script in fields | Markup in the name, company and notes is escaped for Slack and does not change the decision. | `matched` / persona `ciso` / #booth-matched |

## Suite: `returning-visitor` (5)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 5 | `evt_9905` | Rachel, second scan - new notes | A second scan of Lead 1 exercises the returning-visitor thread reply and the last-time notes block. **Likely FAIL against deployed logic: urgency is read only from the current scan's notes, and these notes carry no trigger, so 01 computes matched, not tier_1.** | `tier_1` / #booth-hot |
| 60 | `evt_9960` | Pair A, first scan | Pair A first visit: a CISO with no trigger is matched and creates the encounter. | `matched` / persona `ciso` / #booth-matched |
| 61 | `evt_9961` | Pair A, second scan with new notes escalates | Pair A second visit with a renewal in the new notes escalates the returning visitor to Tier 1. | `tier_1` / persona `ciso` / #booth-hot |
| 62 | `evt_9962` | Pair B, first scan | Pair B first visit: an IAM manager with no trigger is matched. | `matched` / persona `iam` / #booth-matched |
| 63 | `evt_9963` | Pair B, identical second scan | Pair B identical second scan is a returning visitor (scan 2, nothing changed), not a dropped replay. | `matched` / persona `iam` / #booth-matched |

## Suite: `replay` (1)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 59 | `evt_9959` | Same scan_id sent twice - run this suite twice | Same scan_id twice: the second POST answers duplicate_ignored and posts no second card (checked by eye). | `matched` / persona `ciso` / #booth-matched |

## Suite: `caching` (2)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 9 | `evt_9909` | Two people, one company - first scan | First of two scans at one company pays for enrichment and writes the cache row. | `matched` / persona `ciso` / #booth-matched |
| 10 | `evt_9910` | Two people, one company - second scan reuses cache | Second scan at the same company reuses the enrichment cache (run with evt_9909, 30s+ gap). | `matched` / persona `iam` / #booth-matched |

## Suite: `sources` (1)

| # | scan_id | Case | What it proves | Expected outcome |
|---|---|---|---|---|
| 20 | `evt_9920` | Booth notes outrank the company website | Booth-stated headcount outranks the company's own website. | `matched` / persona `iam` / #booth-matched |

## Deliberately not asserted

- **Row 8 and row 38 (ambiguous titles):** the persona and therefore the tier are Gemini's call above a 0.7 confidence floor, so only acceptance and pipeline completion are asserted.
- **Row 18 (homepage language):** depends on a third party's live marketing copy.
- **Row 54 persona:** the rule persona is still recorded as `ciso` on a competitor, but the assertion is limited to the competitor route, which is what the case is about.
- **Score, repeat_scan, scan_count, threading, injection flag, headcount source:** 07 reports these in each result row but has no assertion for them. Read them in the `results` output.

## Harness limits found while writing these cases

- 07 throws if two scans in one run share a scan_id, so a true replay can only be tested across two runs (row 59).
- 07 has no assertion for the webhook `status` (for example `duplicate_ignored`), for the number of Slack cards posted, or for `repeat_scan` / `scan_count` / `slack_threaded`, so replay protection and returning-visitor threading are checked by eye.
- A row without a `scan_id` makes 07's `Build Test Plan` throw for every run, even when the row is disabled, so a missing-scan_id case cannot live in the table.
- 07 matches the stored row on the untrimmed `scan_id`, while 01 trims it, so a padded scan_id would score as "no row" even though 01 stored it.

## Existing expectations that disagree with the deployed logic

Found by running the deployed `Normalize Scan`, `Evaluate ICP Policy`, `Build Visitor Context`, 04 `Build Enrichment Result` and `Enforce Final Deterministic Gates` code locally against every row with enrichment and AI stubbed out. The 43 new rows all agree with the deployed code. Two older rows do not:

- **Row 5 `evt_9905`** expects `tier_1` / #booth-hot, but its notes carry no Tier 1 trigger and 01 reads urgency only from the current scan. Expect `matched` / #booth-matched. If a rep has pressed Not a fit on Rachel and reset is off, the stored override adds a further twist: the gate then sends it to #booth-review as a conflict.
- **Row 17 `evt_9917`** expects `needs_review` / #booth-review, but `Enforce Final Deterministic Gates` sends every `disqualified_under_500` lead to `out_of_scope` and has no exemption for `competitor_status: possible` (04 does have one). Expect `out_of_scope` / #booth-out-of-scope until either the gate or the row changes.
