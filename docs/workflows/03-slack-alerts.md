# 03 - Slack Alerts [sub-flow]

Workflow id `gyCxnkoGkh3OJpz5` · 14 nodes · error workflow is 99 · read from the deployed workflow on 2026-09-28.

## In one breath

03 turns a finished decision into a Slack card a rep can act on in ten seconds. 01 calls it once the tier, CRM context and AI synthesis are settled, and 06 calls it again when a rep reclassifies a lead. It picks the channel from the active policy, builds the card, threads a returning visitor under their first alert, attaches the buttons that tier is entitled to, and hands back a delivery receipt saying exactly what Slack accepted or rejected.

## Trigger, input, output

| | |
|---|---|
| Trigger | `When Called by Sync` (Execute Workflow Trigger, passthrough). Callers: 01 `Notify Slack`, and 06 `Repost Card via Slack Alerts`. |
| Input | The full decision payload: `classification`, `score`, `score_breakdown`, `routing`, `crm_context`, `company_enrichment`, `employee_resolution`, `person_evidence`, `notes_from_booth`, `opening_question`, `listen_for`, repeat-scan fields (`notification_mode`, `previous_slack_ts`, `previous_slack_channel`, `material_change`), and from 06 only `override_banner`. |
| Output | One receipt from `Build Delivery Receipt`: `delivered_count`, `failed_count`, `channel` actually used, `threaded`, `broadcast`, `thread_anchor_recorded`, `owner_dm_requested`, `owner_mapped`, a `deliveries` array (kind, ok, channel, ts, error) and `warnings`. 01 stores it in `oak_deliveries.slack_json`. |
| Side effects | Slack posts (channel, thread reply, owner DM) and one upsert into `oak_encounters` (the thread anchor). |

## Step by step

### Group: Routing Policy

**1. `When Called by Sync`** - Execute Workflow Trigger in passthrough mode. Whatever 01 or 06 sends arrives unchanged. Every later node reads the original lead from here with `$('When Called by Sync').first().json`, so no step can lose a field another step needs.

**2. `Load Routing Policy`** - Data Table get on `oak_policies` where `status = active`, limit 1. This is what makes channels table-driven: the `notifications` block of that row says which channel each classification goes to and whether the owner gets a DM. It is set to `alwaysOutputData` and `continueRegularOutput`, so if the table read fails the flow still runs and falls back to the routing 01 already resolved, or to hard defaults.

**3. `Resolve Slack Targets`** (Code) - the heart of the workflow. It does three jobs: picks the targets, plans threading, and builds the whole card.

- **Channel.** Precedence is: the `routing.channel` 01 already resolved from the policy, then the policy's `notifications[classification].channel`, then `slack.default_channel`, then `booth-review`. Out of scope deliberately gets an empty channel here (step 4 puts it back on the quiet route).
  ```js
  const configuredChannel=clean(routing.channel||(notif[cls]||{}).channel||sl.default_channel||'booth-review');
  const channel=cls==='out_of_scope'?'':configuredChannel;
  ```
- **Owner DM.** Requested when the policy row (or routing) says `owner_dm: true` and the lead is not out of scope. The HubSpot owner id is mapped to a Slack user through `notifications.owner_overrides` or `slack.owner_directory`. No mapping means no DM and a warning such as "Owner DM skipped: HubSpot owner 123 is not mapped to Slack." In the active policy (`oak-event-v5`) only `tier_1` asks for a DM, and both `owner_overrides` and `owner_directory` are currently empty `{}`, so today every Tier 1 owner DM is skipped with that warning until someone fills the map. (The 06 "Send to owner" button does not depend on this map: it bridges HubSpot to Slack by owner email.)
- **Threading.** A returning visitor is threaded only when 01 flagged a repeat and there is a stored anchor from the first alert:
  ```js
  const useThread=Boolean(prevTs&&prevCh&&(mode==='repeat_thread'||mode==='repeat_escalation'));
  ```
  `reply_broadcast` is set to the same value, so a thread reply is also shown in the channel. A repeat must not vanish into a thread.
- **Card assembly, in fixed order.** Each section is a divider, a bold title, then the body, so the eye learns where to look:
  1. **Header**: tier emoji and label, the name, and the one blocker (`- confirm company size` or `- size below ICP, needs a call`). Capped at 150 characters, Slack's header limit.
  2. **Returning visitor** line (repeat only): scan number, first seen, and what changed: tier changed from X to Y, new booth notes, or said out loud, "Nothing has changed since the first visit - same booth notes, same tier."
  3. **Mentions**: `<!here>` "Hot lead at the booth now" for every `tier_1`. On a repeat with `material_change`, `matched`, `needs_review` and `competitor_intel` get one `<!here>` too. Out of scope never does.
  4. **Prospect information**: name, role, email (source: badge scan), HubSpot relationship, owner and open deal count (source: HubSpot), and a public profile quote with its link only if 04 verified one (not for competitors).
  5. **Company information**: company link and domain, size with its source and confidence (for example "12,000 employees, their website, 85% confidence"), industry with source, CRM record created or existing, incumbent tool from booth notes, and one public-context quote with its URL.
  6. **Companies matching this name**: only when size is unknown and 04 found name-only candidates. Up to three, each with its own URL, labelled "Matched on the company name alone ... Not counted in the score - confirm which one at the booth."
  7. Personal email warning if relevant.
  8. **Booth notes** verbatim as a quote, plus the injection warning line if 01 suspected prompt injection, plus **Booth notes last time** on a repeat.
  9. **Why this lead** (or "Why this matters" for a competitor, "Why this is out of scope" for out of scope): numbered reasons for persona, size and urgency, then `Score: persona 30 + urgency 40 + size 15 + industry 15 = 100/100`, then "Decided by: persona from a title rule, which AI cannot override · trigger quoted from the booth notes · size from HubSpot". Out of scope prints `decision_reasons` verbatim instead.
  10. **Do at the booth** (skipped for out of scope): "Open with" the opening question, "Then confirm size" when size is unknown or in the 500-999 band ("Under 500, tap Not a fit"), "Listen for" up to three items, and for competitors a fixed question plus a "Do not discuss" line.
  11. **Links** row: Open HubSpot, Company site (not for out of scope).
  12. **Missing from the scan** context line if any fields were missing, "not guessed".
- **Escaping.** Every user-supplied string goes through `esc()` which turns `& < >` into entities. A booth note cannot inject `<!channel>` or break the block JSON.
- **Output.** One item per target. `delivery_kind` is `channel_new`, `channel_thread` or `dm`. With no target at all it returns one item with `delivery_kind: 'none'` and a `skip_reason`.

**4. `Enforce Out-of-Scope Delivery`** (Code, per item) - the quiet route. For anything other than `out_of_scope` it passes the item through untouched. For out of scope it sets the channel to `routing.channel` or `booth-out-of-scope`, removes every `actions` block and any "Do at the booth" section, and makes it a normal channel post (or a thread reply for a repeat).
```js
parsed.blocks=(parsed.blocks||[]).filter(function(b){if(b.type==='actions')return false; ...
```
Why it exists: the card builder already omits coaching for out of scope, but this is a belt-and-braces guard so no future edit can leak a call to action onto the audit channel. The result: out-of-scope visits get a record, not a ping. No `@here`, no coaching, no buttons.

**5. `Attach Core Action Buttons`** (Code, per item) - decides which buttons a card gets.
- First, if 06 passed an `override_banner`, it inserts that one line right under the header ("*Moved from Needs review to Tier 1* by @rep - confirmed renewal."), so a repost reads as a correction, not a duplicate.
- Out of scope returns here with no buttons.
- Buttons appear only when the n8n variable `OAK_SLACK_ACTIONS_ENABLED` is `true`. Each button's `value` carries `event_person_key`, `scan_id`, `hubspot_contact_id` and `classification`, which is everything 06 needs.
- The layout per classification:

| Card | Row `oak_core_actions` | Row `oak_triage_actions` | Row `oak_status_actions` | Row `oak_more_actions` |
|---|---|---|---|---|
| `competitor_intel` | Why this score? · Flag for competitive intel · False alarm | - | - | - |
| `needs_review` | Claim · Why this score? | **Promote to Tier 1 · Matched · Not a fit** | Mark met · Meeting booked | Add note · Send to owner · Snooze |
| `matched` | Claim · Why this score? | - | Mark met · Meeting booked · Not a fit | Add note · Send to owner · Snooze · Promote to Tier 1 |
| `tier_1` | Claim · Why this score? | - | Mark met · Meeting booked · Not a fit | Add note · Send to owner · Snooze |
| `out_of_scope` | none | - | - | - |

  Claim only appears when there is a HubSpot contact id, because claiming writes to the contact. The triage row exists because a review card asks one question, "where does this lead belong", so its three answers sit side by side.
  ```js
  const triage=cls==='needs_review';
  if(triage)add('oak_triage_actions',[btn('oak_promote_tier1','Promote to Tier 1',null,'primary'),btn('oak_mark_matched','Matched'),btn('oak_downgrade','Not a fit',null,'danger')]);
  ```
- **Duplicate action_id fix.** Slack rejects the whole message with `invalid_blocks` if two elements share an `action_id`. Any repeat gets a `__2` suffix. 06 routes by longest prefix, so the suffixed id still reaches the right handler.

### Group: Deliver and Record

**6. `Route Delivery`** (Switch on `delivery_kind`)
- Output "New channel alert" (`channel_new`) goes to `Post New Alert`.
- Output "Thread reply" (`channel_thread`) goes to `Reply in Thread`.
- Output "Direct message" (`dm`) goes to `Send Direct Message`.
- Fallback "No delivery" goes to `Record No Delivery`.

A `tier_1` first scan with a mapped owner produces two items, so one goes to the channel and one to the DM in the same run.

**7a. `Post New Alert`** (Slack, channel by name) - posts the block card. Retries 3 times, 2 seconds apart, and soft-fails (`continueRegularOutput`) so a Slack outage does not kill the run; the failure is recorded instead.

**8a. `Slack Post Succeeded?`** (IF) - true only when Slack answered `ok: true` and returned a message timestamp.
```js
$json.ok === true && Boolean($json.message?.ts || $json.message_timestamp)
```
True branch goes to `Record Thread Anchor`. False branch goes straight to the receipt. This is the rule that a thread anchor is only written after Slack confirms the root message, so a failed alert can never orphan later replies.

**9a. `Record Thread Anchor`** (Data Table upsert on `oak_encounters` by `event_person_key`) - stores `last_slack_channel` and `last_slack_ts`. The next scan of the same person reads these back in 01 and replies in this thread.

**7b. `Reply in Thread`** (Slack, channel by id) - posts into `plan.thread_channel` under `plan.thread_ts` with `reply_broadcast`. Same retry and soft-fail settings. It does not move the anchor, so every repeat stays under the first alert.

**7c. `Send Direct Message`** (Slack, user by id) - DMs the mapped HubSpot owner. Same retry and soft-fail.

**7d. `Record No Delivery`** (Code) - records `delivered: false` and the reason. Kept so the "nothing to deliver" case is an explicit outcome, not silence.

**10. `Build Delivery Receipt`** (Code) - every branch lands here. It reads all three Slack nodes by name, so it sees every attempt no matter which branch ran, and builds the receipt.
- It reads the plan from `Attach Core Action Buttons`, not from `Resolve Slack Targets`, because the out-of-scope node rewrites the channel in between. An earlier version recorded `channel: null` for every out-of-scope card that had in fact been posted.
- Every failure becomes a warning, for example "Slack delivery failed (channel_new): not_in_channel".
- If targets were resolved but nothing was delivered it adds "check the Slack credential".
```js
const anchored=deliveries.some(d=>d.kind==='channel_new'&&d.ok&&d.ts)&&grab('Record Thread Anchor').length>0;
```

## Decisions worth defending

1. **Channels live in the policy table, not the workflow.** Changing a channel is a Data Table edit and the next scan follows it; no redeploy.
2. **03 renders a decision, it never makes one.** Tier, score and classification arrive settled; 03 only decides channel lookup, mention, card order, threading and buttons. A model cannot name a channel or add a mention.
3. **Every line names its source.** Badge scan, HubSpot, their website, public source or AI, so a rep knows what to trust without asking.
4. **Soft-fail Slack, but record everything.** A Slack outage must not lose a lead, and that is only safe because the receipt is stored and 07 fails a test when nothing was delivered.
5. **Anchor only after Slack confirms.** No orphaned thread replies.
6. **Reclassification goes back through this same workflow.** One card builder for every path, so a reposted card always has the right channel and buttons.

## Likely interview questions

**How does a returning visitor show up?** 01 sets `notification_mode` to `repeat_thread` or `repeat_escalation` and passes the stored `last_slack_ts` and `last_slack_channel`. 03 replies in that thread with `reply_broadcast` on, so it also shows in the channel. The card opens with "Returning visitor · scan #2" and says what changed, including "Nothing has changed" so a silent repeat never looks like a bug. A material change on a non-Tier-1 card earns one `@here`.

**How do you change where Tier 1 leads go?** Edit `notifications.tier_1.channel` in the active `oak_policies` row. 01 resolves routing from that row and 03 falls back to it, so nothing in n8n changes. The mention rules are the one exception: they are code in `Resolve Slack Targets`.

**What happens if Slack is down?** Each Slack node retries 3 times, 2 seconds apart, then soft-fails. `Build Delivery Receipt` records every failed delivery with its error, 01 writes the receipt to `oak_deliveries.slack_json`, and the 07 harness fails the test when nothing was delivered. HubSpot is already written by then, so the lead is not lost.

**A returning visitor moved from Matched to Tier 1. Which channel does the second card land in?** The first alert's channel. When a thread anchor exists, the channel target is replaced by `previous_slack_channel`, so the escalation replies in the original `#booth-matched` thread, broadcast to that channel, with the `@here` and "Tier changed from matched to tier 1". The trade-off is one conversation per person over a fresh card in `#booth-hot`; the owner DM, when mapped, still fires. If asked, say you would post a new root card when `repeat_escalation` changes the channel, and link the old thread.

**Why does an out-of-scope visit get a card at all?** It deserves a record, not a ping. It goes to `#booth-out-of-scope` with the reasons printed verbatim, no mention, no coaching and no buttons, and `Enforce Out-of-Scope Delivery` strips any action block even if a future edit adds one. It gives RevOps an audit trail of who was filtered out and why.

**How much of the card did the AI write?** Two slots: the opening question and the listen-fors, both produced in 05 under guardrails. Everything else is rule output or quoted evidence with a URL. The AI can change which policy row is read only by resolving an ambiguous job title in 05; it can never write the row.

**Why the `__2` suffix on action ids?** Slack rejects the entire message with `invalid_blocks` if two buttons share an `action_id`. 03 de-duplicates, and 06 resolves handlers by longest matching prefix, so the suffixed button still works.
