# 02 - HubSpot Sync [sub-flow]

Workflow `WQ2s5hFfpaE8P45o` - "Oak GTM - 02 HubSpot Sync [sub-flow]". Active version `f13df11d`. Error workflow: 99. The current draft differs from the active version only in cosmetic defaults (sticky size, explicit `GET`, explicit `runOnceForAllItems`); the logic below is identical in both.

## In one breath

02 is the only place the automated pipeline writes to HubSpot. 01 calls it after the tier is settled, and only when the scan has a valid email. It resolves the company by domain (creating it if new), finds the contact by email, reads the company's real deals and the owner list, then upserts the contact without overwriting anything a human typed, writes the 18 `oak_*` properties, logs a meeting and a readable note, and hands back a `crm_context` the Slack card uses to link the record and name the owner.

## Trigger, input, output

| | |
|---|---|
| Trigger | Execute Workflow Trigger `When Called by Intake` (passthrough), called by 01's `Sync to HubSpot` |
| Called when | `email_valid` is true (01's `Can Sync Contact?`) |
| Input | The full settled lead payload from 01 (policy removed): identity, classification, persona, score, reasons, trace, `company_enrichment`, `employee_resolution`, evidence, AI fields, and 02A's pre-write `crm_context` |
| Output | The same payload with `crm_context` replaced by the post-write version: `hubspot_contact_id`, `hubspot_contact_url`, `hubspot_company_url`, `owner_id`, `owner_name`, `owner_email`, `customer_status`, `open_deal`, `open_deal_count`, `related_deals`, `oak_properties_written`, engagement and note ids, `lookup_status`, `warnings` |
| HubSpot writes | Company (create, or `numberofemployees` fill), contact upsert, contact PATCH of `oak_*` properties, meeting engagement, note associated to the contact |

## Step by step (execution order)

The flow is one straight line, company first.

### Group "1. Resolve and enrich the company"

1. **When Called by Intake** (Execute Workflow Trigger). Receives the lead payload.

2. **Find Company by Domain** (HubSpot, company `searchByDomain`, limit 1; retry 3x, continue on error). Domain is the company identity. Reads `name`, `domain`, `industry`, `numberofemployees`, `annualrevenue`, `hubspot_owner_id`, `lifecyclestage`.

3. **Resolve Company Write** (Code). Plans the company write; it does not call HubSpot itself.
   - No company found: plan a `create` with `name` and `domain`.
   - Company found, and HubSpot has no `numberofemployees`, and 04 resolved a headcount from a non-HubSpot source: plan an `update` that writes `numberofemployees` only, and note the source and confidence (e.g. "written from booth notes at 90% confidence").
   - Company found, nothing to add: `no_change`.
   - No domain and no name: `skipped_no_identity`.
   - Industry is never written here: HubSpot's `industry` is a fixed enumeration, and mapping a resolved value onto it would be a guess.

   The one-line rule for headcount:
   ```js
   if(resolved!=null&&String(er.source||'')!=='hubspot_company'&&!crmEmp){props.numberofemployees=String(resolved); ...}
   ```

4. **Create or Update Company** (HTTP, method / URL / body taken from the plan; never errors on HTTP status, continue on error). One node covers `POST /crm/v3/objects/companies` (create) and `PATCH /crm/v3/objects/companies/{id}` (enrich). A no-identity scan targets company `0` and 404s harmlessly.

5. **Resolve Company Id** (Code). Settles the company id the contact will be associated to (new id, else existing id). Turns failures into visible warnings: create failed ("The contact will not be associated to a company"), update failed, or no identity. Flags `company_created` / `company_updated`.

### Group "2. Resolve contact and real deals"

6. **Find HubSpot Contact** (HubSpot contact search, `email` equals the scan email, limit 1; retry 3x, continue on error). Reads the fields `keep()` needs to compare against, plus the rep's event fields `oak_booth_status`, `oak_exclude_from_outreach`, `oak_claimed_by`, `oak_claimed_at`.

7. **Get Company Deal Associations** (HTTP `GET /crm/v4/objects/companies/{id}/associations/deals`). The documented v4 associations endpoint - the authoritative list of deals really attached to this company, not a text search.

8. **Read Associated Deals** (HTTP `POST /crm/v3/objects/deals/batch/read`, up to 20 ids). Reads `dealname`, `dealstage`, `pipeline`, `amount`, `closedate`, `hubspot_owner_id` for those deals. No ids soft-fails to empty.

9. **List HubSpot Owners** (HTTP `GET /crm/v3/owners/?limit=100`). One call per sync, so the card can show a person's name instead of an owner id.

10. **Resolve CRM Write** (Code). The heart of 02: builds the full write plan.
    - **Human beats scanner.** Five free-text fields go through `keep()` - the existing HubSpot value wins, the scan only fills a blank:
      ```js
      const keep=(current,incoming)=>String(current==null?'':current).trim()||String(incoming==null?'':incoming).trim();
      ```
      Applied to `firstname`, `lastname`, `jobtitle`, `company` (HubSpot company name preferred over the scanner's), `website` (`https://<domain>`).
    - `lifecyclestage`: existing contact keeps its value (or `lead` if blank); new contact gets `lead`. `hs_lead_status`: existing keeps its value (or `NEW`); new gets `NEW`.
    - **The 20 `oak_*` values** (18 are sent - see step 12): `oak_person_key`, `oak_lead_tier`, `oak_lead_score`, `oak_persona`, `oak_competitor_status` (enum `none` / `possible` / `confirmed`), `oak_incumbent_tools`, `oak_company_size_band`, `oak_industry`, `oak_industry_source`, `oak_decision_reasons` (the trace, one line per rule with its source), `oak_score_breakdown`, `oak_rule_summary`, `oak_booth_status`, `oak_exclude_from_outreach`, `oak_claimed_by`, `oak_claimed_at`, `oak_last_event`, `oak_last_scan_at`, `oak_scan_count`, `oak_policy_version`.
    - **Rep work survives a re-scan.** `oak_booth_status` is carried forward (default `scanned` only if blank), and `oak_claimed_by` / `oak_claimed_at` are written straight back. `oak_exclude_from_outreach` is one-way: `existing == true OR confirmed competitor`; no scan ever clears it.
    - **Meeting**: title "Oak booth scan - <name> (<classification>)", starts at `scanned_at`, 15 minutes long.
    - **Note body** with labelled sections: OAK BOOTH ENCOUNTER (tier, persona, score, rule fired, who decided the persona), PERSON [Scanner + HubSpot], BOOTH NOTES [Scanner], COMPANY RECORD (headcount with source and confidence, or "VERIFY AT BOOTH"; resolved industry with URL), DECISION PROVENANCE (every trace line, breakdown, size band, incumbents, competitor match, pain signals "evidence, not scored"), RECOMMENDED CONVERSATION [AI synthesis, not fact], VERIFIED COMPANY CONTEXT (only sourced quotes with URLs; quotes mentioning breach / ransom / attack claims are filtered out so an allegation never lands in the CRM as fact).
    - **Owner**: contact owner, else company owner, else the owner 02A found; `owner_source` records which.
    - **Relationship**: `net_new` (no contact), `customer`, `open_opportunity`, or `known_prospect` from the contact's lifecycle stage.
    - `needs_follow_up_task: false` - no task is ever created here.

### Group "3. Write contact, tags and booth history"

11. **Upsert Contact** (HubSpot contact upsert on email; retry 3x, continue on error). Sends the `keep()`-resolved standard fields and `associatedCompanyId`. Upsert, not create, so scanning the same badge twice never makes a second contact. Returns `vid` and `isNew`.

12. **Write Oak Properties API** (HTTP `PATCH /crm/v3/objects/contacts/{vid}`). Writes the Oak properties in one call. Before sending it removes `oak_rule_summary` and `oak_score_breakdown` (not provisioned in the portal) and prepends their content to `oak_decision_reasons` as "Rule fired: ... / Score breakdown: ...", then turns booleans into strings. That leaves 18 properties. The PATCH is all-or-nothing: one bad value and HubSpot rejects all 18, which is why `oak_competitor_status` sends the bare enum value and the competitor's name lives in the note.

13. **Log Booth Conversation** (HubSpot engagement, type `meeting`, associated to the contact). The encounter appears on the contact timeline as a meeting with the full note as internal notes.

14. **Create Associated Booth Note** (HTTP `POST /crm/v3/objects/notes`, association type `202` note-to-contact, `hs_timestamp` = scan time). A readable note, newlines turned into `<br>`.

15. **Build CRM Context** (Code). Assembles what goes back to 01. Collects warnings from every step ("Contact upsert failed", "Oak property write failed", "No HubSpot owner is assigned", company warnings). Resolves the owner id to a name and email from the owners list. Builds the contact URL (`https://app.hubspot.com/contacts/<portal>/record/0-1/<id>`) and company URL (`0-2`). Sets `lookup_status` to `complete` only if a contact id came back and the upsert did not error. `oak_properties_written` is read from the PATCH response, not assumed from the node having run.

## Decisions worth defending

- **Reads and writes are split.** 02A reads before AI, 02 writes after the decision. A lookup can never mutate a record, and the model sees the CRM without being able to touch it.
- **Human-typed HubSpot values win.** `keep()` means the scanner only fills blanks; a rep's corrected title or name is never overwritten, and booth status and claims are carried forward.
- **Email is the contact key, domain is the company key.** Upsert on email is the task's dedupe requirement; one company per domain keeps "two people from one company" on one record.
- **CRM beats enrichment.** A web headcount is written to the company only when HubSpot has none, and HubSpot's `industry` enum is never guessed; resolved industry goes to `oak_industry` with `oak_industry_source`.
- **Deals via the v4 associations endpoint.** "Open deals" means deals genuinely attached to the company, which is what a write-side decision needs. 02A accepts the looser search on the read side.
- **No invented follow-up task.** The only task creator in the system is the Slack Snooze button in 06, so nothing makes up an SLA nobody agreed to.

## Likely interview questions

**How do you avoid duplicate contacts?**
The contact is upserted on email, never created. 01 only calls 02 when the email is valid, so there is always a dedupe key. A second scan of the same badge updates the same contact and bumps `oak_scan_count`.

**What if the rep already edited the contact in HubSpot?**
The five free-text fields go through `keep()`, so any non-empty HubSpot value is written back unchanged and the scan only fills blanks. Lifecycle stage and lead status only default for a brand-new contact. `oak_booth_status`, `oak_claimed_by` and `oak_claimed_at` are read off the contact and written back, so a claimed lead does not reset to `scanned`.

**Why are the Oak fields written by an HTTP PATCH and not the HubSpot node?**
So field-level validation errors are visible in the response and land in `crm_context.warnings`. It matters because the PATCH is all-or-nothing: when `oak_competitor_status` was sent as "confirmed: Veza", HubSpot returned 400 INVALID_OPTION and dropped the tier, persona, score and reasons with it. Now the enum gets a bare value and `oak_properties_written` is asserted from the response.

**Why not write industry to the company?**
HubSpot's `industry` is a fixed enumeration (`BANKING`, `COMPUTER_SOFTWARE`, ...). Mapping "financial_services" onto it would be a guess that corrupts segmentation. The resolved value goes on the contact as `oak_industry` with its source, and in the note with the URL.

**What does the rep see from this?**
The Slack card gets the real HubSpot contact link, the owner's name instead of an id, the relationship (`net_new`, `known_prospect`, `open_opportunity`, `customer`), the open deal, and any warning. In HubSpot they get a meeting on the timeline and a note with the booth notes, the rule that fired, the score breakdown, sourced context and the suggested question.

**What happens when HubSpot is down?**
Every HubSpot call retries and continues on error, so the run keeps going and each failure becomes a line in `crm_context.warnings` that the card shows. `lookup_status` becomes `failed` if no contact id came back. The lead is still classified, stored and alerted by 01.
