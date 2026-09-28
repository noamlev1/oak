# 00 - Setup & Seed

Workflow `PeoS0NF1f7FHzEID` - "Oak GTM - 00 Setup & Seed". Draft only, never published (`activeVersionId` null by design). It has a manual trigger and runs from the editor.

## In one breath

00 builds everything the engine needs before the first scan: the six `oak_*` Data Tables, the active `oak-event-v5` policy row, 17 competitor identities, the 20-scenario test suite, the missing `oak_*` HubSpot contact properties, and the six Slack channels the policy names. Nothing calls it and it calls nothing; you run it by hand on a fresh import and can rerun it safely. It ends with a readiness report listing what was created, what already existed, and what still needs a human.

## Trigger, input, output

| | |
|---|---|
| Trigger | Manual (`Run Setup`) |
| Input | None. All seed data is in the code nodes |
| Output | One report item: `ok`, `policy_version`, `tables`, `competitors_seeded`, `test_payloads_seeded`, `hubspot_properties` (`already_present`, `created`, `missing_after_setup`, `ready`), `slack_channels` (`created`, `already_existed`, `failed`, `ready`), `crm_tagging_ready`, `alerting_ready`, `warnings`, `next` |
| Side effects | Creates Data Tables (if missing), upserts policy / competitor / test rows, marks other policy versions `superseded`, creates HubSpot contact properties, creates Slack channels |

## Step by step (execution order)

### Group "Create Portable Storage"

1. **Run Setup** (Manual Trigger).
2. **Create oak_policies** - columns `version`, `status`, `document_json`, `activated_at`.
3. **Create oak_competitors** - `name`, `domains_json`, `aliases_json`, `category`, `active`, `verification_status`, `source_note`, `updated_at`.
4. **Create oak_deliveries** - one row per scan: identity, `notes_hash`, `classification`, `score`, `policy_version`, `status`, `raw_json`, `slack_json`, `created_at`.
5. **Create oak_encounters** - one row per person per event: identity, `scan_count`, `scan_history_json`, first/last scanned, `last_notes_hash`, `last_classification`, `last_score`, `last_slack_channel`, `last_slack_ts`, claim fields, provenance JSON columns, `last_payload_json`, and the rep-action fields 06 writes (`booth_status`, `exclude_from_outreach`, `override_classification`, `competitor_override`, `competitive_intel`, `downgrade_reason`, `follow_up_at`, `last_action`, `last_action_by`, `last_action_at`).
6. **Create oak_enrichment_cache** - `company_domain`, `company_name`, `enriched_at`, `expires_at`, `source`, `payload_json`, `evidence_json`, `status`.
7. **Create oak_test_payloads** - `suite`, `label`, `enabled`, `sort_order`, `payload_json`, `expect_json`, `notes`.

   All six use "create if not exists", so a rerun reuses the existing table. No other workflow creates a table, which is why this must run first. The idempotence is by name only: an existing table is never migrated.

### Group "Validate and Seed Policy Data"

8. **Build Default Policy** (Code, execute once). Builds the full `oak-event-v5` policy document:
   - `company_fit`: `qualified_employee_min` 1000, `review_employee_min` 500, `disqualified_employee_max` 499, target industries `financial_services`, `fintech`, `technology`, `healthcare`, disqualifiers `saas-only`, `single-cloud`, the personal email domains.
   - `personas`: `ciso`, `iam`, `compliance` (title patterns, 30 points, Oak angle, listen-fors) and `other`.
   - `relevance_signals` (24 terms, `relevance_signals_are_scored: false`).
   - `competitors`: `confirmed_names`, `possible_keywords` (homepage language for 04), `possible_name_tokens` (company-name tokens for 01).
   - `incumbent_tools`: SailPoint, Saviynt, Omada Identity, One Identity, Okta Identity Governance, Microsoft Entra ID Governance, Ping Identity - all active, all `renewal_trigger: true`.
   - `urgency.renewal_max_months` 6.
   - `scoring`: persona 30, company size 15, industry 15, urgency 40, max 100.
   - `notifications`: `tier_1` `booth-hot` (owner DM), `matched` `booth-matched`, `needs_review` `booth-review`, `competitor_intel` `competitive-intel`, `ops_alerts` `oak-revops`, `mention_tiers` `['tier_1']`. `out_of_scope` starts with an empty channel here.
   - `slack.private_channels` `['competitive-intel']`, `enrichment` (30-day cache TTL, search switches, employee confidence: HubSpot 1, booth notes 0.9, official site 0.85, domain-matched search 0.7), `llm` (gemini, temperature 0, `oak-persona-v5`, `minimum_confidence` 0.7), `security` (header `x-oak-webhook-secret`), `competitor_dont_say`.
   - Emits one row: `version`, `status: active`, `document_json`, `activated_at` now.

9. **Enable Out-of-Scope Route** (Code). Parses the document (throws if it is invalid JSON) and sets `notifications.out_of_scope` to `{channel:'booth-out-of-scope', owner_dm:false}`. Keeps the quiet out-of-scope audit route in policy data rather than hard-coded routing.

10. **Seed Active Policy** (upsert `oak_policies` on `version`). Writes the row as `active`. Rerunning overwrites hand edits to that version's row - that is intended: the code is the permanent source, the row is the live-edit surface.

11. **Retire Superseded Policies** (update `oak_policies` where `version != 'oak-event-v5'`, set `status: superseded`; continue on error). Guarantees only one active row, which is what 01's `status = active, limit 1` lookup relies on.

12. **Build Competitor Seed** (Code, execute once). 17 rows: Veza, SailPoint, Saviynt, Omada Identity, One Identity, Okta Identity Governance, Microsoft Entra ID Governance, Ping Identity, CyberArk, BeyondTrust, Delinea, Astrix Security, Oasis Security, Silverfort, Aembit, Token Security, Clutch Security. Each carries domains (empty for Okta and Microsoft, whose corporate domains are too broad), aliases, category (`identity_security`, `iga`, `pam`, `nhi_ai_identity`), `active: true`, `verification_status`, and a source note. It throws on a duplicate domain or alias, so a bad seed fails loudly at setup instead of misrouting a scan later.

13. **Seed Competitors** (upsert `oak_competitors` on `name`).

14. **Build Test Payload Seed** (Code, execute once). The 20 scenarios 07 replays: the four task leads (enabled) and sixteen opt-in ones - returning visitor, personal email, invalid scan, ambiguous title, two-people-one-company cache, 500-999 band, prompt injection, compliance persona, Tier 1 by audit deadline / breach / Saviynt renewal, unlisted competitor by name, small identity vendor, no email, booth notes outranking the website. Each has `payload_json`, `expect_json` and notes. It throws on a duplicate `sort_order` or invalid JSON.

15. **Seed Test Payloads** (upsert `oak_test_payloads` on `sort_order`). `sort_order` is stable and unique, so a drifted label is corrected rather than duplicated. A rerun resets `enabled` to the default four.

16. **Setup Complete** (Code). Starts the report: tables, `competitors_seeded`, `test_payloads_seeded`, `rerunnable: true`.

### Group "Provision HubSpot and Slack"

17. **List HubSpot Contact Properties** (HTTP `GET /crm/v3/properties/contacts`; retry, continue on error). Reads the portal's contact schema so provisioning only creates what is missing.

18. **Plan Oak Properties Raw** (Code). Defines 20 `oak_*` properties and keeps only those the portal does not have. `oak_competitor_status` is an enumeration with exactly `none` / `possible` / `confirmed`; `oak_exclude_from_outreach` is a boolean checkbox; the rest are text, textarea or number. Records a schema-read error if the GET failed.

19. **Plan Oak Properties** (Code). Removes the two retired properties, `oak_score_breakdown` and `oak_rule_summary`, so 18 are provisioned. Their content is folded into `oak_decision_reasons` by 02.

20. **Properties Missing?** (IF `missing_count > 0`).
    - True: **Create Oak Properties** (HTTP `POST /crm/v3/properties/contacts/batch/create`, adds Yes/No options to the boolean; continue on error), then on to Slack.
    - False: straight to Slack.

21. **Plan Slack Channels** (Code). Reads the channel names from the policy's `notifications` block (after the out-of-scope patch), dedupes them, and marks `competitive-intel` private. Result: `booth-hot`, `booth-matched`, `booth-review`, `competitive-intel` (private), `booth-out-of-scope`, `oak-revops`. The workspace always matches the routing rules because both come from the same document.

22. **Create Slack Channel** (Slack channel create, one call per channel; continue on error). `name_taken` on a rerun counts as already provisioned. It does not join existing channels.

23. **Report Setup Result** (Code). Final report: properties already present / created / still missing (with the scope to fix: `crm.schemas.contacts.write`), channels created / already existed / failed (scopes `channels:manage`, `groups:write`), `crm_tagging_ready`, `alerting_ready`, warnings, and next steps (map HubSpot owners to Slack ids in `slack.owner_directory` for DMs; activate 01 and point the scanner at its webhook).

## Decisions worth defending

- **The ICP is data, not code.** Thresholds, personas, triggers, channels, competitors and weights live in one `oak_policies` row that every workflow reads at run time. A live tweak is a row edit; a permanent change is an edit here plus a rerun.
- **Idempotent by design.** Tables are create-if-missing, rows are upserts on stable keys, properties are created only if absent, and `name_taken` is success. It is safe to rerun mid-event by someone unsure of the state.
- **Exactly one active policy.** Upsert by version plus "retire every other version" means 01's single-row lookup can never pick the wrong policy.
- **Fail loudly at setup, not at the booth.** Duplicate competitor domains or aliases, duplicate test ids and invalid JSON throw here.
- **The test suite ships with the export.** Seeding the 20 scenarios means an import is testable immediately; only the four brief leads are enabled by default, so a default harness run is exactly the task.
- **Channels come from the policy.** Plan Slack Channels derives names from `notifications`, so routing and workspace cannot drift apart.

## Likely interview questions

**Why is this a separate workflow and why is it never published?**
It is one-time provisioning with a manual trigger; nothing calls it, so it has no reason to be active. It runs from the draft. Keeping it separate means the hot path in 01 never carries setup logic.

**How would I change the ICP for the next event?**
For a live change, edit `document_json` on the active `oak_policies` row; the next scan uses it with no deploy. For a permanent change, edit Build Default Policy and rerun 00, which upserts the new version as active and marks every other version superseded.

**What happens if I run it twice?**
Nothing doubles. Tables are reused, policy, competitors and tests are upserted on version, name and `sort_order`, only missing HubSpot properties are created, and existing Slack channels return `name_taken`, which is treated as provisioned. The one deliberate overwrite is the active policy row and the test `enabled` flags.

**Why 18 HubSpot properties and not 20?**
`oak_rule_summary` and `oak_score_breakdown` were retired because 02 writes all Oak properties in one all-or-nothing PATCH. Their content is prepended to `oak_decision_reasons` instead, so no information is lost and there are fewer ways for the PATCH to fail.

**Why is the competitor list so wide?**
All 17 are active because a missed competitor at the booth costs more than a false positive. False positives are handled by the False alarm button on the competitor card, and `verification_status` records how each row was established.
