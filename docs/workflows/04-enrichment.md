# 04 - Company & Person Enrichment [sub-flow]

Workflow id `K9MaVwjIoBmZ0oS9` · 20 nodes · error workflow is 99 · read from the deployed version `9742a40a`.

## In one breath

04 learns what it can, for free, about the visitor's company and the visitor, and refuses to claim anything it cannot source. 01 calls it for every classified scan (`Enrich Company`); it reads the company homepage every time, adds Brave public search unless the lead is out of scope, resolves headcount and industry through a fixed confidence ladder, and hands the lead back with sourced evidence, a resolved size band, updated score and, where the evidence demands it, a changed classification.

## Trigger, input, output

| | |
|---|---|
| Trigger | `When Called for Enrichment` (Execute Workflow Trigger, passthrough), called by 01 `Enrich Company`. |
| Input | The classified lead from 01 plus the read-only HubSpot context from 02A: `company_domain`, `company_name`, `full_name`, `job_title`, `classification`, `score`, `score_breakdown`, `crm_context`, `employee_count_observed` (from booth notes), `competitor_status`, `persona_reasoning_required`, repeat-scan fields, and any `rep_override`. |
| Output | The same lead plus `company_enrichment` (with `employee_resolution`, `industry_resolution`, `headcount_name_only_candidates`), `company_evidence`, `person_evidence`, `person_enrichment`, `company_size_band`, updated `classification`, `routing`, `score`, `score_breakdown`, `decision_reasons`, `decision_trace`, `notification_mode`, `enrichment_warnings`, `evidence_review`. |
| Side effects | One upsert per domain into `oak_enrichment_cache`. Up to three Brave searches. No CRM writes. |

## Step by step

### Group: Cache Check

**1. `When Called for Enrichment`** - passthrough trigger. Every later node reads the original lead back from here by name.

**2. `Load Company Cache`** - Data Table get on `oak_enrichment_cache` where `company_domain` equals the scan's domain, limit 1. Soft-fails and always outputs, so a table problem means "no cache", not a dead run.

**3. `Load Active Policy`** - Data Table get on `oak_policies` where `status = active`. Supplies the TTL, the confidence ladder, target industries, disqualifiers, competitor keywords and scoring points. Also soft-fails.

**4. `Assess Cache`** (Code) - decides whether this company needs fetching at all. A cached row is only "fresh" when all three hold: status `complete`, not past `expires_at`, and not rejected. It is rejected if the cached site was parked or for sale, or if it predates identity checking.
```js
const rejected=Boolean(cachedPayload.site_quality==='parked'||parking.test(String(cachedPayload.headline||''))||cachedPayload.evidence_identity_checked!==true);
const fresh=Boolean(row&&String(row.status||'')==='complete'&&notExpired&&!rejected);
```
It also sets `has_company_identity` (a domain or a company name exists), the TTL (policy `company_cache_ttl_days`, 30), and `fetch_url = https://<domain>`. A personal email with no company name gets an explicit "nothing to enrich against" note.

### Group: Enrich and Store

**5. `Cache Fresh?`** (IF on `cache_fresh`)
- True: skip every company fetch and search, go straight to `Build Enrichment Result` using the cached payload and evidence. Two people from the same company in one event cost one enrichment.
- False: go to `Fetch Company Homepage`.

**6. `Fetch Company Homepage`** (HTTP GET) - the free read. Plain browser-like headers, 8 second timeout, 5 redirects, `neverError` so a 404 comes back as data, retry twice, soft-fail. No API key and no paid provider, which is why it runs for every lead including out of scope.

**7. `Extract Company Signals`** (Code) - reads the HTML with regexes. Strips scripts and styles, grabs title, meta description and body text (first 20,000 characters), then:
- **Parked-domain check** ("buy this domain", sedo, dan.com ...). A parked page yields no signals and becomes a fetch error.
- **Competitor language**: matches the policy's `competitors.confirmed_names` (Veza, SailPoint, CyberArk ...) and `possible_keywords` ("identity governance platform", "identity security platform" ...). Any hit sets `possible_competitor: true`. This is what powers the competitor rescue in step 13.
- **Industry** from the title and meta only (financial services, fintech, technology, healthcare keyword maps).
- **Identity stack** and **compliance** keywords (SSO, zero trust, SOX, HIPAA, DORA, NIS2 ...), each saved as a quoted evidence sentence.
- **Headcount** if the page says something like "12,000 employees".
- **Disqualifiers** from the policy (`saas-only`, `single-cloud`).
- `status` is `complete` only if the page was really fetched and not parked, otherwise `partial`, so a failed fetch is never served from cache later.

**8. `Search Enabled?`** (IF) - the paid-search gate. Three conditions: policy `enrichment.search_enabled` is not false, the scan has a company identity, and the classification is not `out_of_scope`.
```js
$('When Called for Enrichment').first().json.classification !== 'out_of_scope'
```
- True: run `Search Company Web`.
- False: skip to `Extract Search Signals`, which then works with site data only.

This is the cost gate: nobody will pursue an out-of-scope lead, so a Brave credit on it is wasted. The free homepage read has already run either way.

**9. `Search Company Web`** (Brave, 5 results, US, English, past-year freshness, web and news) - global search for the company plus identity, compliance and employee terms. Retries twice, soft-fails: if Brave is down the lead still gets site-only enrichment.

Know this if asked: the query expression has a second, competitor-flavoured variant ("product launch AI agents non-human identity funding acquisition leadership") chosen by `$json.classification === 'competitor_intel'`. But `$json` here is the output of `Extract Company Signals`, which carries no `classification`, so that variant never fires and competitor leads get the identity query too. The company name in the query is also the homepage `<title>` name (falling back to the scan's name). The fix is to read `$('When Called for Enrichment').first().json.classification` and `.company_name`.

**10. `Extract Search Signals`** (Code) - turns hits into evidence, with **identity alignment** as the gate on every result. A result is kept only if its host matches the resolved domain, or its text contains the full company name. Everything else is discarded and counted.
- Headcount may only come from a **domain-matched** result. A name match is not entity resolution: "Apex Financial Services" returns a 29-person advisory, a 1,000-person fintech and a 19,379-person group.
```js
const domainAligned=scored.filter(function(a){return a.domainMatch;}).map(function(a){return a.row;});
```
- News signals (breach, audit pressure, identity program, growth event, AI adoption) and industry from aligned results, each kept with its URL.
- Sets `employees_source` to `official_website` if the homepage had a number, else `domain_matched_public_search`, else `not_established`, and stamps `evidence_identity_checked: true` so the cache row is trusted next time.

**11. `Need Headcount Search?`** (IF) - pays for a second search only if no public source has a headcount yet, search is enabled, the scan has a company identity, and the lead is not out of scope.
- True: `Search Company Headcount`.
- False: straight to `Add Name-Only Size Candidates`.

Honest caveat on the canvas note: the HubSpot employee count is not checked at this gate (it is applied later), so a company already sized in the CRM can still spend this credit.

**12. `Search Company Headcount`** (Brave, 8 web results) - a plain "company number of employees headcount" query. The main search is tuned for identity signals and pulls vendor articles, which rarely quote a headcount.

**13. `Add Name-Only Size Candidates`** (Code) - keeps name-only matches that quote a headcount as **labelled candidates**, up to three. It drops generic words ("financial", "services", "group", "bank" ...) so "Apex Financial Services" must match on "apex", skips anything on the company's own domain (that is already a real source), and reads counts like "19,379 employees", "Employees: 19,379" or "employs over 19,379". Candidates are written to `headcount_name_only_candidates` and **never** to `employees_observed`, so they appear on the card for a rep to confirm but can never move the score or the tier.

**14. `Write Company Cache`** (Data Table upsert on `oak_enrichment_cache` by `company_domain`) - one row per domain with `payload_json`, `evidence_json`, `status`, `source` and `expires_at` (now plus 30 days). Soft-fails.

**15. `Build Enrichment Result`** (Code) - where enrichment becomes decisions. Both the cache-hit and cache-miss paths land here.

- **Industry ladder:** HubSpot company industry (confidence 1.0) → official site (0.9) → domain or name aligned public search (0.7) → unknown. 05 may fill an unknown one later from a supplied URL.
- **Headcount ladder**, first hit wins, confidences read from the policy's `employee_confidence` block:

| Rung | Source | Confidence |
|---|---|---|
| 1 | HubSpot company record | 1.0 |
| 2 | What the visitor said at the booth (`employee_count_observed`) | 0.9 |
| 3 | The company's own website | 0.85 |
| 4 | Domain-matched public search | 0.7 |
| - | Nothing | not established, confirm at the booth |

```js
if(crmEmp!==null){...empConf=Number(empCfg.hubspot_company||1);}else if(scannerEmp!==null){...empCfg.booth_notes||0.9...}else if(pubEmp!==null&&pubIsOfficial){...0.85...}else if(pubEmp!==null){...0.7...}
```
  Why booth notes beat the website: the visitor is first-party and present and can be asked, while an About page can carry a stale or global figure; the decision is a coarse band, so a rough human answer lands on the right side of 500 or 1,000 more reliably. Booth notes sit below HubSpot because they are the one source with no URL. Anything below 1.0 (`employee_verification_required_below: 1`) is flagged `needs_verification`, so the card asks the rep to confirm.
- **Size band:** `qualified_1000_plus` (1,000+), `review_500_999`, or `disqualified_under_500`, from policy `qualified_employee_min` and `review_employee_min`.
- **Competitor rescue**, checked first:
```js
if(enrichment.possible_competitor===true&&competitorStatus!=='confirmed'&&classification!=='competitor_intel'){competitorStatus='possible';classification='needs_review'; ...}
if((sizeOut||disq.length)&&classification!=='competitor_intel'&&competitorStatus!=='possible'){classification='out_of_scope'; ...}
```
  A size gate asks "is this company big enough to sell to", the wrong question about a competitor. A small identity vendor would be filed out of scope unseen. So competitor language on the homepage routes to `needs_review` and suppresses the size and disqualifier rule. A language hit is not a verdict; it buys a human look.
- **Size and disqualifier rules:** under 500 or a disqualifier signal moves the lead to `out_of_scope`; 500-999 moves it to `needs_review`; 1,000+ from a non-booth source adds a reason line with its confidence.
- **Scoring:** +15 company size for the 1,000+ band and +15 industry for a target industry, never for out-of-scope or competitor leads, capped at the policy max of 100. Each "add" is `max(points - current, 0)`, so nothing is double-counted.
- **Incumbent tools** (SailPoint, Saviynt, Omada ...) detected in notes or sourced evidence.
- **Rep override on a re-scan:** if a rep pressed Not a fit or False alarm earlier and this lead will skip 05 (`persona_reasoning_required` false), the override is applied here, because 01's final gate only runs on the 05 path. The rep wins unless HubSpot or the visitor (confidence 0.9 or higher) now puts the company in the 1,000+ band; then it goes to `needs_review` and "Neither side wins silently - a human decides."
- **Notification mode:** `first_scan`, `repeat_thread`, or `repeat_escalation` when the classification changed since the last scan. 03 uses this to thread.

**16. `Person Search Worth It?`** (IF) - classification is not `competitor_intel` and not `out_of_scope`.
- True: `Search Relevant Person`.
- False: straight to `Attach Person Evidence`, which then records "unavailable".

A competitor card is about their product, not one contact, and nobody pursues an out-of-scope lead. Note this runs on the cache-hit path too; the cache saves company searches, not the person search. The policy has an `enrichment.person_search_enabled` flag (currently `true`), but this gate does not read it, so turning it off in the table would not stop person searches.

**17. `Search Relevant Person`** (Brave, 3 results) - `"Full Name" Company Title identity security governance LinkedIn`. Soft-fails.

**18. `Attach Person Evidence`** (Code) - keeps a result only when **both** the surname and the company name or domain appear in its title, snippet or URL, max three. Otherwise status is `no_identity_aligned_result` and the card says nothing matched both. It never guesses a biography.

**19. `Sanitize Public Evidence`** (Code) - the last node, so its output is what 01 receives. Drops any company evidence with no quote, no URL, or incident language:
```js
const unsafe=/\b(breach|breached|ransom|cybercriminal|attack(?:er)? claims?|alleged|unconfirmed|data leak|customer data exposed|security incident)\b/i;
```
Public incident claims never reach Slack or Gemini as booth facts. Homepage evidence gets the official URL attached so it survives the URL rule. Reports `evidence_review` counts and rejection reasons.

## Decisions worth defending

1. **Zero cost by design.** Homepage plus Brave through n8n gateway credits, no paid provider. Swapping in Clearbit later is one node behind this sub-workflow; routing and scoring do not change.
2. **The free read runs for everyone, paid searches do not.** Both company Brave gates and the person gate exclude `out_of_scope`; a fresh cache row skips company searches entirely.
3. **A near-name match is worse than no answer.** Headcount only from a domain-matched source; name-only matches are shown as labelled candidates and never scored.
4. **Each source carries its own confidence**, from the policy, so the card can say "85% confidence, confirm at the booth" instead of stating a guess as fact.
5. **Unknown size never disqualifies.** It becomes a question on the card.
6. **Competitor language outranks the size rule**, so a small competitor is seen by a human instead of filed away.

## Likely interview questions

**Where does headcount come from, and what if sources disagree?** First hit wins down a ladder: HubSpot 1.0, booth notes 0.9, the official website 0.85, domain-matched public search 0.7. The values are in the policy's `employee_confidence` block, not code. Anything under 1.0 is flagged for verification, and the card tells the rep to confirm it.

**How do you control cost?** Three gates. A fresh cache row (30-day TTL, one row per domain) skips all company fetching. `Search Enabled?` and `Need Headcount Search?` both require `classification !== 'out_of_scope'`, and the headcount search only runs if nothing public found a size. The person search is skipped for competitors and out of scope. Worst case is three Brave calls per scan; the homepage read is free.

**How do you avoid attaching the wrong company's data?** Every search result must match the resolved domain or the full company name, and headcount must come from the company's own domain. "Apex Financial Services" returns three unrelated companies from 29 to 19,379 people; those appear only as labelled candidates that cannot change the score. Person results need surname plus company.

**What is the competitor rescue?** If the free homepage read finds competitor names or platform language from the policy, the lead becomes `competitor_status: possible` and goes to `needs_review` before the size rule runs, and the size and disqualifier rule is suppressed. Otherwise a 200-person identity vendor would be filed as out of scope and nobody would see a competitor walked into the booth.

**What happens when Brave or the website is down?** Every external node retries and soft-fails. The lead is still classified, scored and alerted, with a warning such as "Public search was unavailable" on the payload. A failed homepage fetch is cached as `partial`, which is never treated as fresh, so the next scan tries again.

**Why strip breach language from public evidence?** An unverified allegation in front of a prospect is a liability. `Sanitize Public Evidence` drops any company evidence quoting breach, ransom, alleged or security incident, so it never reaches Slack or the model. Urgency from a breach only counts when the visitor said it at the booth.
