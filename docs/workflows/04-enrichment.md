# 04 - Company & Person Enrichment [sub-flow]

Workflow id `K9MaVwjIoBmZ0oS9` · 20 nodes · error workflow is 99 · read from the deployed workflow on 2026-09-28.

## In one breath

04 learns what it can, for free, about the visitor's company and the visitor, and refuses to claim anything it cannot source. 01 calls it for every classified scan (`Enrich Company`); it reads the company homepage every time, adds Brave public search unless the lead is out of scope, asks Gemini whether a company whose site talks about identity sells identity security, resolves headcount and industry through a fixed confidence ladder, and hands the lead back with sourced evidence, a resolved size band, updated score and, where the evidence demands it, a changed classification.

## Trigger, input, output

| | |
|---|---|
| Trigger | `When Called for Enrichment` (Execute Workflow Trigger, passthrough), called by 01 `Enrich Company`. |
| Input | The classified lead from 01 plus the read-only HubSpot context from 02A: `company_domain`, `company_name`, `full_name`, `job_title`, `classification`, `score`, `score_breakdown`, `crm_context`, `employee_count_observed` (from booth notes), `competitor_status`, `persona_reasoning_required`, repeat-scan fields, and any `rep_override`. |
| Output | The same lead plus `company_enrichment` (with `employee_resolution`, `industry_resolution`, `headcount_name_only_candidates`), `company_evidence`, `person_evidence`, `person_enrichment`, `company_size_band`, updated `classification`, `routing`, `score`, `score_breakdown`, `decision_reasons`, `decision_trace`, `notification_mode`, `enrichment_warnings`, `evidence_review`. |
| Side effects | One upsert per domain into `oak_enrichment_cache`. Up to three Brave searches. At most one Gemini call (the vendor check), none on a fresh cache hit. No CRM writes. |

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
- **Competitor language**: matches the vendor names in the `oak_competitors` table (active rows only, read by the `Load Competitors` node, the same table 01 uses) and the policy's `possible_keywords` ("identity governance platform", "identity security platform" ...). The table is the one list of vendors: add or switch off a vendor there and both 01 and 04 follow. Aliases are not used here, because short aliases like "okta" or "oasis" appear on many customers' websites. A vendor that uses shorter phrases is left to the AI vendor check (step 14).
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

Know this if asked: a `competitor_intel` lead gets a competitor-flavoured query instead ("company overview product launch AI agents non-human identity funding acquisition leadership"), because a competitor card is about their product and moves, not their IGA pain. The choice reads the classification from the trigger input, `$('When Called for Enrichment').first().json.classification`. The company name in the query is the homepage `<title>` name, falling back to the scan's name when the site is parked or unreachable; the headcount search in step 12 uses the scan's name, so the two searches come at the company from both spellings.

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

**14. `Build Vendor Check`** (Code) - decides whether to ask Gemini if this company is an identity security vendor. The keyword lists miss vendors that describe themselves in short phrases: conductorone.com says "identity governance" and "identity security" but none of the long `possible_keywords` phrases. The check runs for every classification except a confirmed competitor, and only when the homepage is usable and already shows identity language (a policy identity signal or a `possible_name_tokens` phrase). It is on by default; policy `llm.ai_competitor_check: false` turns it off (the key is not in the policy row today).

**15. `AI Vendor Check?`** (IF) - true goes to `Gemini Vendor Check`; false skips the call and the lead is unchanged.

**16. `Gemini Vendor Check`** - `gemini-3-flash-preview` through n8n gateway credits, temperature 0, no tools, strict JSON. One question: does this company sell identity security software? It answers `{vendor, confidence, product, quote}`.

**17. `Apply Vendor Verdict`** (Code) - the answer counts only if `vendor` is true, `confidence` is at least policy `llm.minimum_confidence` (0.7), and the quote is found in the website text. Then it sets `possible_competitor: true` on the payload, so the cache keeps the flag for the domain for 30 days. Anything else (not a vendor, low confidence, a quote not on the site, Gemini down or garbage) leaves the lead unchanged. The outcome is recorded in `payload.ai_vendor_check.status`. It never confirms or clears a competitor; the rep does that with Flag for competitive intel or False alarm.

**18. `Write Company Cache`** (Data Table upsert on `oak_enrichment_cache` by `company_domain`) - one row per domain with `payload_json`, `evidence_json`, `status`, `source` and `expires_at` (now plus 30 days). Soft-fails.

**19. `Build Enrichment Result`** (Code) - where enrichment becomes decisions. Both the cache-hit and cache-miss paths land here.

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
  A size gate asks "is this company big enough to sell to", the wrong question about a competitor. A small identity vendor would be filed out of scope unseen. So competitor language on the homepage, or a counted AI vendor verdict, routes to `needs_review` with rule "Review: possible competitor" and suppresses the size and disqualifier rule. This holds for a lead that was out of scope at the scan too; its old "Out of scope: ..." headline is replaced. The AI verdict adds the reason "AI read <domain> as an identity security vendor (<NN>% confidence, <product>): "<quote>". A human must confirm before outreach." Neither a language hit nor an AI verdict decides anything; each buys a human look.
- **Size and disqualifier rules:** under 500 or a disqualifier signal moves the lead to `out_of_scope`. 500-999 only adds a reason line (trace `company_500_999`): the tier stands on persona and trigger, with no company-size points. 1,000+ from a non-booth source adds a reason line with its confidence.
- **Sources disagree on size:** if the booth notes put the company under 500 (so 01 said `out_of_scope`) but a higher-confidence source such as HubSpot resolves 500 or more, a target persona with no SaaS-only or single-cloud disqualifier goes to `needs_review`, rule "Review: sources disagree on company size", trace `company_size_conflict`, reason "Sources disagree on size: the booth notes put the company under 500, but <source> says <N>. A human decides."
- **Scoring:** +15 company size for the 1,000+ band and +15 industry for a target industry, never for out-of-scope or competitor leads, capped at the policy max of 100. Each "add" is `max(points - current, 0)`, so nothing is double-counted.
- **Incumbent tools** (SailPoint, Saviynt, Omada ...) detected in notes or sourced evidence.
- **Rep override on a re-scan:** if a rep pressed Promote, Matched, Not a fit or False alarm earlier and this lead will skip 05 (`persona_reasoning_required` false), the override is applied here, because 01's final gate only runs on the 05 path. The rep wins. The one exception is Not a fit: if HubSpot or the visitor (confidence 0.9 or higher) now puts the company in the 1,000+ band, it goes to `needs_review` and "Neither side wins silently - a human decides." A confirmed large headcount agrees with Promote and Matched, so it never contradicts them.
- **Notification mode:** `first_scan`, `repeat_thread`, or `repeat_escalation` when the classification changed since the last scan. 03 uses this to thread.

**20. `Person Search Worth It?`** (IF) - classification is not `competitor_intel` and not `out_of_scope`, and the policy's `enrichment.person_search_enabled` is not `false` (a missing flag counts as on).
- True: `Search Relevant Person`.
- False: straight to `Attach Person Evidence`, which then records "unavailable".

A competitor card is about their product, not one contact, and nobody pursues an out-of-scope lead. Note this runs on the cache-hit path too; the cache saves company searches, not the person search. The policy flag `enrichment.person_search_enabled` (currently `true`) switches person search off for everyone with a row edit.

**21. `Search Relevant Person`** (Brave, 3 results) - `"Full Name" Company Title identity security governance LinkedIn`. Soft-fails.

**Recent news (`Search Company News`, Brave, same n8n Cloud search credits).** Runs right after `Search Company Web`, only for leads that get a company search at all (never out of scope). It searches the exact company name, news only, past year. `Extract Search Signals` keeps at most two items that are on the company's own domain or name the full company, drops incident claims ("breach", "alleged", "data leak" ...) exactly as `Sanitize Public Evidence` does, and stores them as `recent_news` with date and link. They are put first in the evidence so 05 can use them in the opening question, and 03 shows them as a "Recent news" line. They never feed headcount, industry or the classification. The company search itself asks for news too, but with its identity-heavy query Brave almost never returns any, which is why news has its own query.

**22. `Attach Person Evidence`** (Code) - keeps a result only when it carries the visitor's **first name and surname as whole words** and either the **email or company domain** or a **distinctive company word**, max three. Generic words that many companies share (services, group, financial, technologies, security ...) do not count: "Rachel Green - TEAM Services Group" used to pass for Apex Financial Services on the word "services". Otherwise status is `no_identity_aligned_result` and no person claim is made.

**23. `Sanitize Public Evidence`** (Code) - the last node, so its output is what 01 receives. Drops any company evidence with no quote, no URL, or incident language:
```js
const unsafe=/\b(breach|breached|ransom|cybercriminal|attack(?:er)? claims?|alleged|unconfirmed|data leak|customer data exposed|security incident)\b/i;
```
Public incident claims never reach Slack or Gemini as booth facts. Homepage evidence gets the official URL attached so it survives the URL rule. Reports `evidence_review` counts and rejection reasons.

## Decisions worth defending

1. **Zero cost by design.** Homepage plus Brave and one Gemini vendor check through n8n gateway credits, no paid provider. Swapping in Clearbit later is one node behind this sub-workflow; routing and scoring do not change.
2. **The free read runs for everyone, paid searches do not.** Both company Brave gates and the person gate exclude `out_of_scope`; a fresh cache row skips company searches entirely.
3. **A near-name match is worse than no answer.** Headcount only from a domain-matched source; name-only matches are shown as labelled candidates and never scored.
4. **Each source carries its own confidence**, from the policy, so the card can say "85% confidence, confirm at the booth" instead of stating a guess as fact.
5. **Unknown size never disqualifies.** It becomes a question on the card.
6. **Competitor language outranks the size rule**, so a small competitor is seen by a human instead of filed away.

## Likely interview questions

**Where does headcount come from, and what if sources disagree?** First hit wins down a ladder: HubSpot 1.0, booth notes 0.9, the official website 0.85, domain-matched public search 0.7. The values are in the policy's `employee_confidence` block, not code. Anything under 1.0 is flagged for verification, and the card tells the rep to confirm it. One disagreement goes to a human: if the booth notes say under 500 but HubSpot says 500 or more, a target persona goes to `needs_review` instead of out of scope.

**How do you control cost?** Three gates. A fresh cache row (30-day TTL, one row per domain) skips all company fetching. `Search Enabled?` and `Need Headcount Search?` both require `classification !== 'out_of_scope'`, and the headcount search only runs if nothing public found a size. The person search is skipped for competitors and out of scope. Worst case is three Brave calls per scan; the homepage read is free. The Gemini vendor check runs only when the homepage already uses identity language, never for a confirmed competitor, and its answer is cached with the domain for 30 days.

**How do you avoid attaching the wrong company's data?** Every search result must match the resolved domain or the full company name, and headcount must come from the company's own domain. "Apex Financial Services" returns three unrelated companies from 29 to 19,379 people; those appear only as labelled candidates that cannot change the score. Person results need surname plus company.

**What is the competitor rescue?** If the free homepage read finds competitor names or platform language from the policy, or the AI vendor check reads the site as an identity security vendor with a quote from the site, the lead becomes `competitor_status: possible` and goes to `needs_review` before the size rule runs, and the size and disqualifier rule is suppressed. Otherwise a 200-person identity vendor would be filed as out of scope and nobody would see a competitor walked into the booth. That is what happened to Casey Lin at ConductorOne ("under 200 people"): the site says "identity governance" but none of the long policy phrases. The AI check now sends that lead to review. It never confirms a competitor; the rep does.

**What happens when Brave or the website is down?** Every external node retries and soft-fails. The lead is still classified, scored and alerted, with a warning such as "Public search was unavailable" on the payload. A failed homepage fetch is cached as `partial`, which is never treated as fresh, so the next scan tries again.

**Why strip breach language from public evidence?** An unverified allegation in front of a prospect is a liability. `Sanitize Public Evidence` drops any company evidence quoting breach, ransom, alleged or security incident, so it never reaches Slack or the model. Urgency from a breach only counts when the visitor said it at the booth.
