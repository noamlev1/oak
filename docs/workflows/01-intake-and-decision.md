# 01 - Intake & Decision

Workflow `9SzVHK4vdv7Z3UMH` - "Oak GTM - 01 Intake & Decision". Active version `7d48dcb0`. Error workflow: 99 (`N4JW7CO6qjdLi6mq`).

## In one breath

01 is the orchestrator that sits behind the booth webhook. The badge scanner POSTs a scan. 01 checks the secret, validates the scan, blocks replays, classifies the lead with deterministic rules read from the policy table, and answers the scanner with a provisional 202 in about two seconds. After that reply it calls 02A (CRM read), 04 (enrichment) and 05 (AI), runs a final deterministic gate, calls 02 (HubSpot write) and 03 (Slack card), and stores the final decision in `oak_deliveries` and `oak_encounters`. The stored row is the real answer, not the HTTP reply.

## Trigger, input, output

| | |
|---|---|
| Trigger | Webhook `POST /webhook/oak-event/scan` (node `Receive Booth Scan`, response mode "Using Respond to Webhook node", CORS `*`) |
| Auth | Header `x-oak-webhook-secret` must equal the n8n variable `OAK_WEBHOOK_SECRET`. If the variable is not set, auth is open and `auth_mode` says `open_no_secret_configured` |
| Input body | `scan_id`, `first_name`, `last_name`, `email`, `job_title`, `company_name`, optional `company_domain`, `scanned_at`, `notes_from_booth`, optional `event_id` (default `oak-live-event-2026`) |
| HTTP replies | `401` unauthorized, `400` invalid (no `scan_id`, or no identity at all), `200` `duplicate_ignored` (replayed `scan_id` that already completed), `202` `accepted` with provisional decision |
| Durable output | `oak_deliveries` row per `scan_id` (status `accepted` then `complete`, `raw_json` = full final payload, `slack_json` = Slack receipt). `oak_encounters` row per `event_person_key` (scan count, history, final classification, `last_payload_json`) |
| Side effects via sub-flows | HubSpot contact / company / meeting / note (02), Slack card or thread reply (03) |

## Step by step (execution order)

### Group "1. Accept the scan"

1. **Receive Booth Scan** (Webhook). Receives the POST. It does not reply itself; a later Respond node does. That is what lets 01 answer early and keep working.

2. **Normalize Scan** (Code, once per item). Turns the raw body into one clean object. What it does:
   - Reads the secret header and computes `auth_ok`. If `OAK_WEBHOOK_SECRET` is set, the header must match exactly; if not set, everything passes.
   - Lowercases and validates the email with a simple `x@y.z` regex. An invalid email becomes `''` and `email_valid:false`.
   - Company domain: uses `company_domain` if supplied (strips `http(s)://`, `www.`, paths). If not supplied, infers it from the email domain, but never from a personal domain (gmail.com, googlemail.com, outlook.com, hotmail.com, live.com, yahoo.com, icloud.com, proton.me, protonmail.com). Records the source (`scanner` / `email_domain` / `unknown`).
   - Company name: uses the supplied one, or builds one from the domain (`apex-finance.com` becomes "Apex Finance").
   - Builds `event_person_key` = `event_id|email`, or `event_id|scan:<scan_id>` when there is no valid email. This is the key for "same person at the same event".
   - Hashes the notes (FNV-1a, lowercased) into `notes_hash` so a later scan can tell whether the notes changed.
   - Lists soft gaps in `data_quality.missing_fields` (email, name, company, job_title, notes). These never block.
   - Fatal errors only: no `scan_id`, or no email AND no first name AND no last name AND no company. Those set `valid:false`.
   - Why it exists: every later node reads one normalized shape, and the only hard rejections are decided in one place.

3. **Authorized?** (IF on `auth_ok`). True goes on. False goes to **Respond Unauthorized**, which returns `401` with `status: unauthorized` and "Missing or incorrect x-oak-webhook-secret header." Nothing is stored, enriched or alerted. Auth is read in code rather than with the webhook's built-in auth because it has to run before the policy load and still return a clean JSON body.

4. **Valid Scan?** (IF on `valid`). True goes on. False goes to **Build Invalid Response** (`accepted:false`, `status:'invalid'`, `errors` = the reason codes `scan_id` / `person_identity`) and **Respond Invalid** with HTTP `400`. Nothing is written - `status: invalid` exists only in the reply, so the scanner app can tell the rep what is missing.

5. **Find Existing Scan** (Data Table get on `oak_deliveries`, filter `scan_id` = this scan, limit 1, always output data). Looks up the ledger row for this `scan_id`. "Always output data" means "no row" still produces an empty item, so the flow continues.

6. **Duplicate Delivery?** (IF `status == 'complete'`).
   - True: the scan already ran end to end. **Build Duplicate Response** + **Respond Duplicate** return `200` with `status: duplicate_ignored`. No CRM write, no Slack post.
   - False: new scan, or a scan that died mid-pipeline (still `accepted`). It runs again on purpose - a retry is how a half-finished scan recovers.

7. **Load Active Policy** (Data Table get on `oak_policies`, `status = active`, limit 1). Loads the single active policy row (`oak-event-v5`). Everything the rules use - personas, thresholds, channels, scoring - comes from its `document_json`. Set to always output, like every other lookup here: with no active row the scan still gets its reply, and Evaluate ICP Policy runs on its code defaults, which hold no personas, so every scan lands in `out_of_scope` (or `competitor_intel` from the competitor table) until a policy is active again.

### Group "2. Classify and spot returning visitors"

8. **Load Competitors** (Data Table get on `oak_competitors`, `active` is true, return all; always output, continue on error). The competitor register is data: add a row and the next scan honours it with no workflow edit. 17 rows today.

9. **Evaluate ICP Policy** (Code). The first routing decision, all deterministic. In order:
   - **Competitor check.** For each active competitor row: exact match of `company_domain` against `domains_json`, or a whole-word match of the company name against the row name and `aliases_json`. First hit sets `competitor` with `matched_on: domain | company_name`.
   - **Possible competitor by name.** If no confirmed competitor, whole-word match of the company name against `policy.competitors.possible_name_tokens` ("identity security", "identity governance", "access governance", "identity platform", "privileged access", "non-human identity", "machine identity", "identity fabric", "identity management", "access management").
   - **Persona.** Walks the policy personas in order `ciso`, `iam`, `compliance`; the first whose `title_patterns` appears as a substring of the lowercased job title wins. Otherwise `other`.
   - **Relevance signals.** Policy `relevance_signals` (identity, iam, iga, access review, sailpoint, non-human identities, service accounts, api keys, ai agents, sox, audit, compliance, ...) found in title or notes. Recorded as evidence; never scored (`relevance_signals_are_scored: false`).
   - **Headcount from notes.** Regex `(\d[\d,]{1,8})\s*(employees|people|staff|seats|users)`. Size band: `>= 1000` is `qualified_1000_plus`, `>= 500` is `review_500_999`, below is `disqualified_under_500`, nothing found is `unknown`.
   - **Disqualifiers.** Policy `company_fit.disqualifiers` = `saas-only`, `single-cloud`, substring match in the notes.
   - **Incumbent tools.** Whole-word match in the notes against the 7 active policy incumbents (SailPoint/IdentityIQ, Saviynt, Omada, One Identity, Okta Identity Governance, Microsoft Entra ID Governance, Ping Identity), all `renewal_trigger: true`.
   - **Tier 1 triggers (urgency)**, each quoted from the notes:
     - `incumbent_renewal`: a renewal-in-N-months phrase, N <= `urgency.renewal_max_months` (6), AND an incumbent tool matched. The original patterns (`renewal ... in 5 months`, `5 months ... renewal`) run first; if neither matches, a second pass accepts renew / renews / renewing / renewed / renewal before the months, or renew / renews / renewing / renewal after them, with number words one to twelve, "a year" (12) and "half a year" (6). "SailPoint renews in four months" and "renewal coming up in five months" trigger; "renewed 2 months ago", "renewal in Q1", "next spring" and "in a month" do not. A renewal with no matched tool is logged as `unverified_renewal` and does not trigger.
     - `identity_breach`: "breach", "breaches", "breached" or "post-breach" (optionally after identity / security / data / cyber / credential / account), unless a negation (never, no, not, none, without, zero, or haven't / hasn't / hadn't / didn't / wasn't / weren't / isn't / aren't) sits within the three words before it in the same clause, or the clause ends "no history of" / "free of" / "free from" plus up to two words. The contract sense is excluded: "breach of contract / agreement / SLA / terms / NDA / warranty / covenant / duty / confidentiality", a breach preceded by contract / contractual / SLA / agreement / warranty / covenant, and "breach-free". "We had a breach last quarter" and "post-breach audit" are triggers; "we have never had a security breach" and "a breach of contract" are not. The phrases are in code, not the policy row.
     - `audit_deadline`: "audit" within 30 characters of "deadline/due/active", "deadline ... audit", or "sox ... deadline/due/active".
   - **The classification cascade** - first match wins:

     ```js
     if(competitor){classification='competitor_intel';}
     else if(possibleNameHit){classification='needs_review';}
     else if(outBySize||dqHits.length){classification='out_of_scope';}
     else if(targetPersona&&reviewBySize){classification='needs_review';}
     else if(targetPersona){classification=urgency.length?'tier_1':'matched';}
     else if(relevanceSignals.length){classification='needs_review';}
     // else stays 'out_of_scope'
     ```

     | # | Condition | Result | `persona_status` |
     |---|---|---|---|
     | 1 | Domain or name matches `oak_competitors` | `competitor_intel` | `competitor` |
     | 2 | Company name carries a possible-competitor token | `needs_review` | unchanged (`irrelevant`) |
     | 3 | Known under 500 employees, or `saas-only` / `single-cloud` in notes | `out_of_scope` | `matched_but_disqualified` or `irrelevant` |
     | 4 | Target persona AND 500-999 employees | `needs_review` | `matched_fit_review` |
     | 5 | Target persona AND a Tier 1 trigger | `tier_1` | `matched` |
     | 6 | Target persona, no trigger | `matched` | `matched` |
     | 7 | No persona, but identity/compliance context | `needs_review` | `ambiguous` |
     | 8 | Nothing relevant | `out_of_scope` | `irrelevant` |

     Order matters: rule 2 sits above the size rule because mistaking a competitor for a small bad fit is the expensive mistake. Unknown headcount never demotes anyone.
   - **Score** (provisional): persona 30 if a target persona, company size 15 only if the notes put it at 1,000+, urgency 40 if any Tier 1 trigger was found, industry 0 (industry is resolved later by 04/05). Capped at `scoring.max` 100. The score explains the decision; it does not route it. The tier comes from the cascade, not from the number.
   - **Reasons and trace.** Every rule that fired adds a readable sentence to `decision_reasons` and a `{code, detail, source}` entry to `decision_trace` (codes like `competitor_identity`, `company_under_500`, `persona_rule`, `incumbent_renewal`, `company_size_unverified`). `rule_summary` is the one-liner, e.g. "Tier 1: CISO + SailPoint renewal <= 6 months".
   - **Routing** from `policy.notifications[classification]`: channel, `owner_dm`, and `mention` if the tier is in `mention_tiers` (`tier_1` only).
   - **Flags:** `intelligence_required` (true for all five classifications), `persona_reasoning_required` (true only for `tier_1`, `matched`, `needs_review`), `injection_suspected` (regexes for "ignore previous instructions", "disregard ... instructions", "system prompt", `<system>`, "pretend to be"), `notes_untrusted: true`.

10. **Load Prior Encounter** (Data Table get on `oak_encounters` by `event_person_key`, always output). Finds this person's row for this event, if any.

11. **Build Visitor Context** (Code). Returning-visitor logic.
    - `scan_count` = prior count + 1; `repeat_scan` = count > 1.
    - `classification_changed` = prior `last_classification` differs from today's; `notes_changed` = prior `last_notes_hash` differs.
    - `notification_mode`: `first_scan`, `repeat_thread` (same tier - reply in the first card's thread), or `repeat_escalation` (tier changed).
    - Carries `previous_slack_channel` / `previous_slack_ts` so 03 can reply in the original thread, and `previous_notes_from_booth` so the card shows what they said last time.
    - Appends this scan to `scan_history` (kept to the last 20).
    - Reads the rep's earlier decision from the row: `rep_override` (`override_classification`), its reason, who and when, and `exclude_from_outreach`. These feed the final gate.
    - Carries earlier urgency forward. A Tier 1 trigger the visitor gave on an earlier scan at this event is still true today, so the `urgency_evidence` in the prior `last_payload_json` is added to this scan's (tagged `source: earlier_scan` with the scan it came from; a trigger this scan already quotes is not duplicated), with urgency points if this scan had none and trace `urgency_carried_forward`. A `matched` lead becomes `tier_1` with rule "Tier 1: <PERSONA> + <trigger> (earlier scan)", exactly as Evaluate ICP Policy would have decided. Only urgency is carried: persona, size and competitor checks come from this scan, so a returning visitor now under 500 or in the 500-999 band stays where the rules put them, and a rep's Not a fit is still applied by the final gate. `classification_changed` is computed after this, so a Tier 1 visitor who comes back is a `repeat_thread`, not an escalation to matched.

12. **Load Company Cache** (Data Table get on `oak_enrichment_cache` by `company_domain`, always output).

13. **Build Alert Package** (Code). Uses the cache row only if `status == 'complete'` and `expires_at` is in the future (30-day TTL set by 04). If the cached public-site language flagged `possible_competitor` and the lead is not already a confirmed competitor or out of scope, it moves the lead to `needs_review` and routes it to the review channel. Builds `crm_context` as `pending`, and the `alert` object (headline, who, why_now, what_to_do, score, evidence, routing). `what_to_do` for a competitor is the policy's "don't say" line plus "capture what they ask about"; for out of scope it is "qualify politely, keep the CRM record, do not escalate".

### Group "3. Record and answer the scanner"

14. **Upsert Encounter** (upsert `oak_encounters` on `event_person_key`). Writes identity, `scan_count`, first/last scanned, `last_notes_hash`, provisional `last_classification`, and the scan history. Rep fields (override, claim) are not in the mapping, so they survive.

15. **Upsert Scan Ledger** (upsert `oak_deliveries` on `scan_id`). Writes the ledger row with `status: accepted`, provisional classification and score, `policy_version`, and a first `raw_json` (scanner input plus decision). `accepted` is what makes a mid-pipeline failure retryable.

16. **Build Accepted Response** (Code). The reply body: `status: accepted`, a note that "decision fields below are provisional ... the stored decision in oak_deliveries is authoritative", then `provisional_classification`, `provisional_persona`, `provisional_score`, breakdown, rule summary, routing, size band, repeat info, `injection_suspected`, cache hit, `policy_version`, `alert_preview`, and the list of downstream steps.

17. **Respond Accepted** (Respond to Webhook, `202`). The scanner gets its answer here, about two seconds in. The workflow keeps running.

### Group "4. Buy intelligence, then settle the tier"

18. **Prepare CRM Handoff** (Code). Removes the `policy` object from the payload before the sub-workflow hops, so the policy document is not copied through every call. Everything else passes on.

19. **Needs Intelligence?** (IF `intelligence_required`). Every classification sets it true, so every classified scan goes to CRM context and enrichment - competitor detection must not depend on lead fit. The false branch (straight to `Can Sync Contact?`) is a safety net that no current classification takes.

20. **Read CRM Context** (Execute Workflow 02A, wait). Read-only HubSpot lookup: contact, company, owner, relationship, open deals. Adds `crm_context`.

21. **Enrich Company** (Execute Workflow 04, wait). Free homepage read always; paid Brave search skipped for `out_of_scope`; person search skipped for competitors. 04 also settles headcount (order: HubSpot 1.0, booth notes 0.9, official site 0.85, domain-matched search 0.7), recomputes the size band, can flag a possible competitor (to `needs_review`), can downgrade on size or disqualifier, adds size and industry points (never for `out_of_scope` / `competitor_intel`), and applies a rep override for leads that will not reach the final gate. For competitor and out-of-scope leads, 04 has the last deterministic word.

22. **Needs AI Synthesis?** (IF `persona_reasoning_required`).
    - True (`tier_1`, `matched`, `needs_review` as classified in step 9): go to AI.
    - False (`competitor_intel`, `out_of_scope`): skip AI and the final gate, go straight to `Can Sync Contact?`. This is the spend gate on Gemini.

23. **Reason with AI** (Execute Workflow 05, continue on error). Bounded Gemini call. It can change the tier in exactly one situation: `persona_status == 'ambiguous'`. At confidence >= 0.7 (policy `llm.minimum_confidence`) it resolves the persona and moves the lead to `matched`, or to `tier_1` if the rules already quoted an urgency trigger; if it is confident there is no persona it moves it to `out_of_scope`; below 0.7 it stays `needs_review`. It never overrides a title the rules matched, and it writes the opening question and listen-fors. If the notes looked like an injection, Gemini is skipped entirely.

24. **Load Gate Policy** (Data Table get on the active `oak_policies` row, always output). Reloads the policy because step 18 dropped it; without this the gate would route with hardcoded fallback channels.

25. **Enforce Final Deterministic Gates** (Code). Runs after AI so the rules always outrank the model. Cascade:

    ```js
    if(x.competitor_status==='confirmed'&&x.competitor){ classification='competitor_intel'; ... }
    else if(x.company_size_band==='disqualified_under_500'&&!possibleCompetitor){ classification='out_of_scope'; ... }
    else if(siteDisq.length&&!possibleCompetitor&&classification!=='out_of_scope'&&classification!=='competitor_intel'){ classification='out_of_scope'; ... }
    else if(x.company_size_band==='review_500_999'&&classification!=='out_of_scope'){ classification='needs_review'; ... }
    else if(possibleCompetitor&&classification!=='needs_review'){ classification='needs_review'; ... }
    else if(classification==='out_of_scope'){ route('out_of_scope', ...) }
    else { route(classification, ...) }
    ```

    - Confirmed competitor: `competitor_intel`, no mention.
    - Size band (as resolved by 04) under 500: `out_of_scope`, unless the lead is a possible competitor, which stays with a human in `needs_review` as it does in 04.
    - SaaS-only or single-cloud found on the company's own site (`company_enrichment.disqualifier_signals`), when 05 has lifted the lead off 04's `out_of_scope`: back to `out_of_scope` with 04's reason "Public evidence indicates ...", rule "Out of scope: <disqualifier>", trace `environment_disqualifier_final_gate`. A possible competitor is exempt, as in 04.
    - 500-999: `needs_review`, with reason "Final deterministic gate kept the lead in review because 500-999 employees is below the qualified 1,000+ threshold."
    - Possible competitor (`competitor_status == 'possible'`) that 05 moved to `matched`, `tier_1` or `out_of_scope` after resolving an ambiguous title: back to `needs_review`, rule "Review: possible competitor", trace `possible_competitor_final_gate`. The model resolves a persona; it does not clear a competitor flag.
    - Otherwise the tier stands and is routed from the policy (`tier_1` to `booth-hot` with mention, `matched` to `booth-matched`, `needs_review` to `booth-review`).
    - Unknown headcount with a target persona: the tier stands, a reason is added, and the rule summary gets "(headcount to confirm)". `unknown_size_requires_review: false` is recorded explicitly.
    - Removes any reason that starts "null employees".
    - **Rep override, last.** If the encounter row carries a rep override from 06 (`tier_1`, `matched`, `out_of_scope` for Not a fit, `needs_review` for False alarm):
      - Same as the gate's answer: keep it, note the rep agreed.
      - Different, and no strong contradiction: the rep wins; routed to the override's channel, no mention, trace `rep_override_honoured`.
      - A Not a fit override (`out_of_scope`), and HubSpot or the visitor (confidence >= 0.9) now put the company at 1,000+: `needs_review`, rule "Conflict: rep marked this ..., but N employees from <source> qualifies the company", trace `rep_override_contradicted`. Neither side wins silently. A Promote, Matched or False alarm override is never contradicted by headcount, because a confirmed large company agrees with it.

      ```js
      const contradicted=ovr==='out_of_scope'&&Number(er.confidence||0)>=0.9&&String(x.company_size_band||'')==='qualified_1000_plus';
      ```
    - Stamps `decision_basis.final_deterministic_gate: true`, `gate_policy_version`, and the override flags.

26. **Persist Final Classification** (upsert `oak_encounters` on `event_person_key`). Writes the settled classification, score, breakdown, reasons, rule summary, sources and evidence, and `last_payload_json`. Needed because AI or the gate may have moved the tier after the 202.

27. **Restore Enriched Payload** (Code). Picks the final-gate output if it has a `scan_id`, else falls back to the `Enrich Company` output, and hands it on.

28. **Can Sync Contact?** (IF `email_valid`). Three arrows land here: from step 19 false, step 22 false, and step 27.
    - True: go to HubSpot.
    - False: skip the CRM write (no email means no safe dedupe key) and go straight to `Settle Final Payload`. Intelligence and Slack still happen.

### Group "5. Write the CRM"

29. **Sync to HubSpot** (Execute Workflow 02, wait). The single automated CRM writer. Returns the payload with the post-write `crm_context` (real contact id and URL, owner name, open deals, warnings).

### Group "6. Settle and persist the record"

30. **Settle Final Payload** (Code). The meeting point for both sides of the email gate. Picks the richest package: the 02 output if it ran, else the incoming item, else `Restore Enriched Payload`. Without this node a no-email scan skipped the persist steps and its ledger row stayed `accepted` forever.

31. **Persist Complete Encounter** (update `oak_encounters` by `event_person_key`, continue on error). Final snapshot of the person: classification, score, reasons, HubSpot contact id, sources, evidence, `crm_context_json`, `last_payload_json`.

32. **Persist Complete Delivery** (update `oak_deliveries` by `scan_id`, continue on error). Sets final classification, score, full `raw_json` (policy removed) and `status: complete`. From this moment a replay of this `scan_id` is a duplicate.

33. **Restore Complete Package** (Code). Re-emits the settled payload so Slack receives the lead object, not the Data Table write result.

### Group "7. Alert the booth"

34. **Notify Slack** (Execute Workflow 03, wait). Posts the card to the routed channel, or replies in the returning visitor's thread; `tier_1` gets `@here` and the owner DM; `out_of_scope` goes quietly to `#booth-out-of-scope` with no buttons. Returns a delivery receipt.

35. **Persist Slack Delivery** (update `oak_deliveries` `slack_json` by `scan_id`, continue on error). Stores what Slack actually did: targets tried, answers, errors. Slack failures are non-fatal, which is only acceptable because this receipt exists and the 07 harness asserts on it.

### The four task leads through the ladder (from the live ledger)

| Lead | What fires | Result | Score |
|---|---|---|---|
| 1 Rachel Green, CISO, "SailPoint, renewal coming up in 5 months" | persona `ciso` + incumbent renewal 5 <= 6 | `tier_1`, `#booth-hot`, @here, "(headcount to confirm)" | 70 (30 + 40) |
| 2 Hiroshi Tanaka, "Lead Identity Infrastructure Architect" | persona `iam` ("identity infrastructure"), no trigger; NHI / API key pain is evidence only | `matched`, `#booth-matched` | 30 |
| 3 David Miller, Head of IT, "SaaS-only shop, 80 employees" | 80 < 500 and `saas-only` | `out_of_scope`, `#booth-out-of-scope` | 0 |
| 4 Sarah Connor, veza.com | domain `veza.com` in `oak_competitors` | `competitor_intel`, `#competitive-intel` | 0 |

## Decisions worth defending

- **Answer 202 before enrichment.** A rep is standing in front of the visitor and a scanner cannot wait 20 seconds for web fetches and an LLM. The reply is labelled provisional; `oak_deliveries` is authoritative.
- **Rules decide, the model describes.** Every tier and channel is deterministic code against the policy. The LLM can only resolve an ambiguous title above 0.7 confidence, and the final gate re-applies size, competitor and rep-override rules after it.
- **Policy lives in a table, not in code.** Personas, thresholds, disqualifiers, triggers, channels and scoring weights are read from the active `oak_policies` row at run time, and competitors from `oak_competitors`. Changing the ICP is a row edit.
- **Replay blocks only completed scans.** Same `scan_id` at `complete` is ignored; at `accepted` it runs again, so a scan that crashed halfway can be recovered by re-sending it. Same person with a new `scan_id` is a returning visitor, always alerted, in the original thread.
- **Unknown headcount never demotes.** The ICP disqualifies companies known to be under 500. An unknown size keeps the tier and becomes a question for the rep ("headcount to confirm").
- **Spend is gated, attention is not.** Every classified scan gets CRM context and the free homepage read, so a small competitor is still caught. Only Gemini (competitors and out of scope skip it) and paid search (out of scope skips it) are gated.

## Likely interview questions

**Walk me through what happens when a scan comes in.**
The webhook hands the body to Normalize Scan, which cleans fields, infers the company domain from a work email, builds the person key and checks the secret. It then passes auth, validation and the replay check, loads the active policy and competitors, and Evaluate ICP Policy classifies with a first-match-wins ladder. It detects a returning visitor, writes the encounter and ledger rows, and replies 202 with the provisional decision. Then it calls 02A, 04 and 05, runs the final gate, writes HubSpot through 02 if there is an email, marks the ledger complete and posts to Slack through 03.

**What is the classification order and why that order?**
Competitor first, then possible competitor by name, then hard disqualifiers (known under 500, SaaS-only, single-cloud), then persona at 500-999 to review, then persona plus trigger to Tier 1, persona alone to matched, identity context without a persona to review, and everything else out of scope. Competitor sits on top because a competitor with a CISO title must never land in #booth-hot. The possible-competitor token sits above the size rule because calling a small competitor "too small" is the costly mistake.

**How is the score computed and does it drive routing?**
Persona 30, company size 15 (only at 1,000+), industry 15 (only when enrichment resolves a target industry), Tier 1 urgency 40; total 100, weights from `policy.scoring`. 01 awards persona, size-from-notes and urgency; 04 and 05 add size and industry later. The score does not route anything - the cascade does - so Rachel is Tier 1 at 70 because persona plus a quoted renewal trigger is the rule, not because 70 crossed a threshold.

**What stops the LLM from promoting a bad lead?**
Four things. It is only called for `tier_1`, `matched` and `needs_review`, and only changes a tier when the persona is `ambiguous`. It needs 0.7 confidence and cannot invent urgency - Tier 1 requires a trigger the regex already quoted from the notes. And Enforce Final Deterministic Gates runs after it and re-applies competitor, size band and rep override from a freshly loaded policy.

**What is the difference between a duplicate and a returning visitor?**
A duplicate is the same `scan_id` whose ledger row already reached `complete`; it gets 200 `duplicate_ignored` and nothing else happens. A returning visitor is the same `event_person_key` (event plus email) with a new `scan_id`; `scan_count` goes up, the mode is `repeat_thread` (reply in the first card's thread) or `repeat_escalation` if the tier changed, and the card shows what they said last time.

**What if a sub-workflow fails?**
Inside each sub-workflow every external call retries and continues on error, so failures become warnings on the payload and the card, not a dead run. Slack's receipt is stored in `slack_json` so a failed post is visible. If something throws hard, the run stops, the 99 error workflow reports it to #oak-revops, and the ledger stays at `accepted`, so re-sending the same `scan_id` runs it again.

**Why is email the gate for the HubSpot write?**
Email is the only safe dedupe key for a contact; without it, 02 would have to guess and could create duplicates. So a scan without an email is still classified, enriched, stored and alerted, and only the CRM write is skipped - Settle Final Payload makes sure the ledger still reaches `complete`.
