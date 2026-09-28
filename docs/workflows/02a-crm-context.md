# 02A - CRM Context [sub-flow]

Workflow `xnl1bav7McaQIwXC` - "Oak GTM - 02A CRM Context [sub-flow]". Active version `d1d32bc2` (same as draft). Error workflow: 99.

## In one breath

02A is the read-only half of the HubSpot pair. 01 calls it right after the 202, for every classified scan, before enrichment and AI. It asks HubSpot what it already knows - portal, company by domain, contact by email, open deals - and hands the same payload back with one `crm_context` object: relationship, owner and where the owner came from, open deals, and the contact URL. It never changes a CRM record.

## Trigger, input, output

| | |
|---|---|
| Trigger | Execute Workflow Trigger `When Called for CRM Context` (passthrough), called by 01's `Read CRM Context` |
| Called when | Every scan that reaches `Needs Intelligence?` true, which is every classified scan |
| Input | The lead payload from 01 (policy removed); uses `company_domain` and `email` |
| Output | Same payload plus `crm_context`: `lookup_status`, `portal_id`, `hubspot_contact_id`, `hubspot_contact_url`, `is_net_new`, `existing_contact`, `company`, `company_match`, `owner_id`, `owner_source`, `customer_status`, `open_deal`, `open_deal_count`, `related_deals`, `warnings` |
| Writes | None. Four GET / search calls |

## Step by step (execution order)

A straight line of five nodes, all in the group **"Read CRM Context"**.

1. **When Called for CRM Context** (Execute Workflow Trigger). Receives the payload.

2. **Read HubSpot Portal** (HTTP `GET https://api.hubapi.com/integrations/v1/me`). Gets the `portalId`, so Slack links point at the right HubSpot portal and region. Retry 3x at 1.5s, continue on error.

3. **Find Company by Domain** (HubSpot company `searchByDomain`, limit 1). Exact domain match. Reads `name`, `domain`, `industry`, `numberofemployees`, `annualrevenue`, `hubspot_owner_id`, `lifecyclestage`. This is what lets 04 treat a HubSpot headcount as the record of truth (confidence 1.0) and industry from the CRM first.

4. **Find Contact by Email** (HubSpot contact search, `email` equals the scan email, limit 1). A missing email searches for the sentinel `__missing_email__`, which safely finds nothing. Reads name, `jobtitle`, `company`, `lifecyclestage`, `hs_lead_status`, `hubspot_owner_id`, `createdate`.

5. **Find Deals by Company** (HubSpot deal search, filter `associations.company` = company id, up to 10, newest first). No company searches for `__no_company__`, which finds nothing. Reads `dealname`, `dealstage`, `pipeline`, `amount`, `closedate`, owner.

6. **Build CRM Context** (Code). Derives the fields downstream actually branches on:
   - **Relationship** from the contact's lifecycle stage:
     ```js
     const customerStatus=!contact?'net_new':lifecycle==='customer'?'customer':lifecycle==='opportunity'?'open_opportunity':'known_prospect';
     ```
   - **Owner**: contact owner, else company owner; `owner_source` = `contact` / `company` / `none`.
   - **Open deals**: drops `closedwon` and `closedlost`, so a company whose only deals are closed reads as zero open deals. `open_deal` is the newest open one.
   - `company_match`: `domain_exact` or `not_found_by_domain`.
   - Contact URL: the one HubSpot returned, else `https://app.hubspot.com/contacts/<portal>/record/0-1/<id>`.
   - Every lookup is read through a try/catch helper and filtered for a real id, so a failed or empty call is simply "not found".

## Decisions worth defending

- **Read before you reason.** Knowing the person is already a customer, or already owned by a rep, changes the booth conversation more than any public fact, so the CRM is read before 05 writes the opening question.
- **Reading and writing are separate workflows.** A lookup that accidentally mutates a record is the fastest way to lose trust in an automation. 02A only reads; 02 does every automated write, after the decision.
- **The CRM is the record of truth.** Headcount, industry and owner already in HubSpot outrank anything enrichment finds; enrichment only fills gaps.
- **Reading the CRM is never gated.** Even an out-of-scope scan gets the CRM read; the cost gates live in 04 (paid search) and 05 (Gemini), because a HubSpot read is free.
- **Looser read, strict write.** Deals come from a search on `associations.company`, which is index-backed and can miss a deal created seconds ago. That is fine for AI context. 02 uses the v4 associations endpoint because a write must not be wrong.

## Likely interview questions

**Why a separate read workflow instead of reading inside 02?**
02 runs at the end, after AI. The AI and enrichment need the CRM context earlier: an existing owner, an open deal or a HubSpot headcount changes the question and the size band. Splitting read and write also makes it impossible for the lookup path to change a record.

**How do you decide customer vs prospect?**
From the contact's lifecycle stage: no contact is `net_new`, `customer` is `customer`, `opportunity` is `open_opportunity`, anything else is `known_prospect`. Owner comes from the contact first, then the company, and `owner_source` records which one answered.

**What happens if HubSpot has no record?**
That is a normal outcome, not an error - it is what `net_new` means. The company comes back `not_found_by_domain`, owner is `none`, deals are zero, and 02 creates the records later if the scan has an email.

**What if a HubSpot call fails?**
Each call retries three times, 1.5 seconds apart, and continues on error, so the pipeline never stops here. A failed lookup is filtered out like an empty one and reads as "not found".

**Why read the portal id?**
So the Slack card's HubSpot link opens the right portal. The contact URL is built from portal id plus contact id when HubSpot does not return one.
