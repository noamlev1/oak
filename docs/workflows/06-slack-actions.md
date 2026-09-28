# 06 - Slack Actions

Workflow id `VsKr9HAqOrtOlxZC` · error workflow is 99 · read from the deployed workflow on 2026-09-28.

## In one breath

06 closes the loop inside Slack: it is the Interactivity endpoint behind the twelve buttons on every booth card, and the four modals some of them open. Slack calls it (not another workflow) when a rep taps a button; it verifies Slack's HMAC signature and fails closed, acknowledges inside Slack's 3-second window, then writes the result to the encounter row and HubSpot, replies in the card's thread and puts a check mark on the card. When a rep reclassifies a lead it reloads the stored decision and calls 03 again, so the reposted card lands in the right channel with the right buttons.

## Trigger, input, output

| | |
|---|---|
| Trigger | `Receive Slack Action` - Webhook `POST /webhook/oak-slack/actions`, raw body kept, response sent by a Respond node. |
| Input | Slack's form-encoded `payload`: a `block_actions` click (button `action_id` and JSON `value` with `event_person_key`, `scan_id`, `hubspot_contact_id`, `classification`) or a `view_submission` from a modal (`callback_id`, form values, `private_metadata`). Headers `x-slack-signature` and `x-slack-request-timestamp`. |
| Output | HTTP 200 (empty) to Slack when verified; 401 on a bad signature; 503 if the signing secret is not configured. Then side effects. |
| Side effects | `oak_encounters` row update; HubSpot contact PATCH, note, task or owner write; Slack thread reply, card update, owner DM, modal, reaction; for reclassification a new card via 03. |

## The twelve buttons

| Button (on the card) | `action_id` | Route | What it writes |
|---|---|---|---|
| Claim | `oak_claim` | Claim | Encounter `claimed_by_*`, `booth_status=claimed`; HubSpot `oak_claimed_by`, `oak_claimed_at`, `oak_booth_status=claimed`, and `hubspot_owner_id` only if empty and your Slack email matches a HubSpot owner. Card gets "Claimed by @you", Claim button removed. |
| Why this score? | `oak_why_score` | Why score | Nothing. Replies in thread with the stored rule, score split and reasons. No LLM. |
| Mark met | `oak_booth_status_met` | Effects (prefix `oak_booth_status`) | `oak_booth_status=met`, `hs_lead_status=CONNECTED` |
| Meeting booked | `oak_booth_status_meeting_booked` | Modal (date + note) | `oak_booth_status=meeting_booked`, `hs_lead_status=OPEN_DEAL`, `lifecyclestage=salesqualifiedlead`; `follow_up_at` = meeting date 09:00 UTC |
| Promote to Tier 1 | `oak_promote_tier1` | Modal (6 reasons + detail) | `oak_lead_tier=tier_1`; encounter `override_classification=tier_1`; **reposts via 03** |
| Matched | `oak_mark_matched` | Effects, no modal | `oak_lead_tier=matched`; **reposts via 03** |
| Not a fit | `oak_downgrade` | Modal (7 reasons + detail) | `oak_lead_tier=out_of_scope`, `oak_exclude_from_outreach=true`, `oak_booth_status=not_a_fit`, `hs_lead_status=UNQUALIFIED`; **reposts via 03** to the quiet channel |
| Add note | `oak_add_note` | Modal (free text, 2,000 chars) | Attributed HubSpot note only |
| Send to owner | `oak_send_owner` | Effects, side `send_owner` | DMs the card, without buttons, to the HubSpot owner |
| Snooze | `oak_snooze` | Effects, side `snooze` | HubSpot task due 09:00 UTC tomorrow; `follow_up_at`; `booth_status=follow_up_later` |
| Flag for competitive intel | `oak_flag_intel` | Effects | `oak_competitor_status=confirmed`, `oak_exclude_from_outreach=true` |
| False alarm | `oak_false_alarm` | Effects, no modal | `oak_competitor_status=none`, `oak_lead_tier=needs_review`, `oak_exclude_from_outreach=false`; **reposts via 03** into review. Does not edit the competitor table. |

A `needs_review` card shows the **triage row** Promote to Tier 1 | Matched | Not a fit (built in 03). Competitor cards show only Why this score?, Flag for competitive intel and False alarm. Out-of-scope cards have none.

## Step by step

### Group: Verify and Route Every Action

**1. `Receive Slack Action`** (Webhook) - `rawBody: true` because the HMAC must be computed over the exact bytes Slack sent, and `responseMode: responseNode` so the workflow decides the status code.

**2. `Verify Signature and Parse`** (Code) - security first, before anything is looked up.
- Fails closed with a named error if `OAK_SLACK_SIGNING_SECRET` is missing, the raw body is missing, or the timestamp is more than 300 seconds from now (replay protection).
- Computes Slack's v0 signature and compares in constant time:
```js
const expected='v0='+crypto.createHmac('sha256',secret).update('v0:'+timestamp+':'+raw,'utf8').digest('hex');
verified=received.length===expected.length&&crypto.timingSafeEqual(Buffer.from(received),Buffer.from(expected));
```
- Only if verified, decodes the form body by hand (`URLSearchParams` is not available in the Code sandbox) and parses `payload`.
- Normalises both shapes: for a click it takes `action_id`, the button `value`, channel, message `ts` and the original blocks; for a modal it takes `callback_id`, the form values and `private_metadata` (which carries the channel and message `ts`, because a `view_submission` has no message).
- Sets `response_code`: 200 verified, 401 bad signature, 503 no secret configured.

**3. `Acknowledge Slack`** (Respond to Webhook) - answers Slack immediately with that code and an empty body. Slack gives an interactive endpoint 3 seconds; everything slow (HubSpot, reposts) happens after this. An empty 200 on a modal submission also closes the modal.

**4. `Continue Only If Verified`** (Code) - `return v.verified?[{json:v}]:[];` An unverified request produces zero items, so nothing downstream runs. This is the fail-closed gate.

**5. `Load Encounter`** - Data Table get on `oak_encounters` by `event_person_key` from the button value. Gives the stored score, reasons, claim state and current overrides.

**6. `Build Action Context`** (Code) - builds the "Why this score?" text from the stored row (`last_rule_summary`, `last_score`, `last_score_breakdown`, `last_decision_reasons`) and maps the action to a route by **longest matching prefix**:
```js
const baseActionId=Object.keys(map).filter(function(k){return rawActionId===k||rawActionId.indexOf(k)===0;}).sort(function(x,y){return y.length-x.length;})[0]||rawActionId;
const route=a.payload_type==='view_submission'?'effects':(map[baseActionId]||'unsupported');
```
Longest prefix does two jobs: `oak_booth_status_met` resolves to `oak_booth_status` (effects) while `oak_booth_status_meeting_booked` resolves to itself (modal), and a `__2` suffix that 03 adds to de-duplicate an id still reaches its handler. Every modal submission routes to `effects`, with its `callback_id` as the action key.

**7. `Route Slack Action`** (Switch on `route`)
- Output 0 "Why score" (`why`) - `Reply With Stored Score`.
- Output 1 "Claim" (`claim`) - `Get Claimant`.
- Output 2 "Open modal" (`modal`) - `Build Modal View`. Add note, Not a fit, Promote to Tier 1, Meeting booked.
- Output 3 "CRM and Slack effects" (`effects`) - `Plan Action Effects`. Mark met, Matched, Snooze, Send to owner, Flag for intel, False alarm, and all four modal submissions.
- Fallback "Unsupported" - `Ignore Unsupported Action`, which stops cleanly rather than half-writing an effect.

### Group: Why this score? - Stored Explanation

**8. `Reply With Stored Score`** (Slack) - posts the explanation in the card's thread: rule fired, score as `x/100 (persona: 30 pts, urgency: 40 pts ...)`, numbered deterministic reasons. Read-only by design: an explanation must never be able to change what it explains, and no model is called at click time.

### Group: Claim - Ownership and CRM Sync

**9. `Get Claimant`** (Slack user info) - the clicker's real name and email.

**10. `Claim Encounter If Unowned`** (Data Table update) - the race guard. The update matches on the person **and** `claimed_by_slack_id` is empty:
```
event_person_key = <key>   AND   claimed_by_slack_id isEmpty
```
Sets `claimed_by_slack_id`, `claimed_by_name`, `claimed_at`, `booth_status=claimed`. When two reps tap at once, the first update fills the field and the second matches no row.

**11. `Reload Claim State`** - reads the row back.

**12. `Build Claim Result`** (Code) - success only if the stored claimant is the person who clicked:
```js
const success=found&&String(row.claimed_by_slack_id||'')===String(a.slack_user_id||'');
```
Otherwise "Already claimed by {name}.", or if no row exists, "That badge is not in the booth record ... Scan it at the booth first."

**13. `Claim Succeeded?`** (IF)
- True: continue to HubSpot.
- False: `Reply Already Claimed` posts the honest "already claimed by" in the thread and stops. The loser writes nothing.

**14. `Find HubSpot Owner by Email`** - HubSpot owners API filtered by the claimant's Slack email.

**15. `Read HubSpot Owner State`** - the contact's current `hubspot_owner_id` and `oak_*` claim fields.

**16. `Build HubSpot Claim Patch`** (Code) - always `oak_claimed_by`, `oak_claimed_at`, `oak_booth_status=claimed`. Adds `hubspot_owner_id` only when the contact has no owner **and** an owner's email equals the claimant's. An existing owner is never overwritten.

**17. `Write HubSpot Claim`** - PATCH the contact. Retries 3 times, soft-fails.

**18. `Build Claimed Card`** (Code) - copies the original blocks, inserts a context line under the header "✅ Claimed by @rep · set HubSpot owner from matching Slack email" (or the HubSpot error), and removes the Claim button so nobody else tries.

**19. `Mark Card Claimed`** (Slack message update) - rewrites the card in place.

**20. `Confirm Claim in Thread`** (Slack) - "Claimed by @rep." in the thread, then on to `Mark Card Handled`.

### Group: Add note, Not a fit, Promote, Meeting booked - Open Modal

**21. `Build Modal View`** (Code) - builds one of four modals, each carrying `event_person_key`, `hubspot_contact_id`, `scan_id`, `channel_id`, `message_ts` and `classification` in `private_metadata` so the submission can write back, reply in the right thread and repost.
- Promote to Tier 1 (`oak_promote_modal`): required reason from confirmed renewal, live identity incident, active audit deadline, budget and timeline, senior decision maker, other; optional 500-char detail.
- Meeting booked (`oak_meeting_modal`): required date picker (defaults to today), optional note.
- Add booth note (`oak_note_modal`): required free text up to 2,000 chars.
- Not a fit (`oak_downgrade_modal`): required reason from outside ICP, wrong persona, too small, no timing or budget, partner/reseller/vendor, student or job seeker, other; optional detail.

**22. `Open Slack Modal`** (HTTP `views.open`) - a `trigger_id` expires in 3 seconds, which is another reason the ack comes first and only one table read happens before this. When the rep submits, Slack sends a fresh `view_submission` into step 1, and it routes to `effects`.

### Group: Decide Effects for Every Other Action

**23. `Plan Action Effects`** (Code) - one planner for the ten buttons on the shared write path. It starts from the current row, so an action never blanks a field another action owns, then per action sets:
- `eu` - the merged encounter row (`booth_status`, `exclude_from_outreach`, `override_classification`, `competitor_override`, `follow_up_at`, `downgrade_reason`, `competitive_intel`, `last_action`, `last_action_by`, `last_action_at`)
- `properties` - the HubSpot patch (only the fields this action owns)
- `note_body` - an audit note that always starts `OAK BOOTH ACTION - ...`, actor, time, and "Source: Slack action button, not AI inference."
- `reply_text` - the thread reply
- `side` - `direct`, `snooze`, `send_owner` or `repost`
- `reclass_reason` - the modal reason, used in the repost banner

Examples: False alarm sets `override_classification='needs_review'`, `side='repost'`; Not a fit sets `out_of_scope`, `exclude_from_outreach=true`, `hs_lead_status: 'UNQUALIFIED'`, `side='repost'`; Snooze builds a HIGH priority TODO task.

**24. `Needs Side Effect?`** (Switch on `side`)
- Output 0 "Snooze task" - `Create Snooze Task`, then the shared write path.
- Output 1 "Send to owner" - `Get Contact Owner` chain, then the shared write path.
- Output 2 "Repost card" - `Load Decision Payload` chain, then the shared write path. Used by Promote to Tier 1, Matched, Not a fit and False alarm.
- Fallback "Write only" - straight to `Apply Encounter Update`. Used by Mark met, Meeting booked, Add note, Flag for intel.

### Group: Snooze - HubSpot Follow-up Task

**25. `Create Snooze Task`** (HTTP POST tasks) - due 09:00 UTC the next day, associated to the contact (association type 204). The only task creator in the system.

### Group: Send to owner - DM Hand-off

**26. `Get Contact Owner`** - the contact's `hubspot_owner_id`.
**27. `Get Owner Record`** - the owner's email, the only reliable bridge from a HubSpot owner to a Slack user.
**28. `Lookup Slack User`** - `users.lookupByEmail` (needs `users:read.email`).
**29. `DM Owner Card`** - `chat.postMessage` to that user: "Forwarded from the Oak booth by @rep because you own this record in HubSpot", then the card **without** its action rows, so the owner cannot double-write from a stale copy.

All four soft-fail; a miss is reported in the thread instead of silently dropping the hand-off.

### Group: Reclassify - Repost the Card Through 03

**30. `Load Decision Payload`** - Data Table get on `oak_deliveries` by `scan_id`. `raw_json` is the exact object 01 handed to 03 for that scan.

**31. `Build Reclassified Payload`** (Code)
- Falls back to `oak_encounters.last_payload_json` if the delivery row is missing.
- If neither parses, or no new classification was planned, it returns `repost_ok: false` with a reason. It never calls 03 with a half-built payload, which would post a card full of blanks.
- Otherwise it sets the new `classification`, **deletes `routing`** so 03 resolves the channel from the policy for the new tier, forces `notification_mode='first_scan'` and clears `previous_slack_ts` and `previous_slack_channel` (a new card in a different channel, not a thread reply), and sets the banner:
```js
out.override_banner=(from&&from!==to?('*Moved from '+label(from)+' to '+label(to)+'*'):('*Reclassified as '+label(to)+'*'))+' by '+String(p.who||actor)+(reason?(' - '+reason):'')+'.';
```
- The rep's free-text reason has `< > &` stripped so it cannot break Slack parsing.
- For Not a fit, `decision_reasons` is replaced with "Marked not a fit at the booth by {rep}" plus the reason, because the out-of-scope card prints reasons verbatim and the rules' reasons would contradict the human.

**32. `Repost Payload Ready?`** (IF on `repost_ok`)
- True: `Repost Card via Slack Alerts`.
- False: skip straight to `Record Repost Outcome`; the CRM and row are still updated and the reply says the repost was skipped.

**33. `Repost Card via Slack Alerts`** (Execute Workflow, 03, wait for completion) - calls 03 exactly as 01 does. 03 looks up the channel for the new tier, renders the full card, attaches that tier's buttons, puts the banner under the header, and records the new thread anchor. Waiting means 03's delivery receipt can be reported in the reply.

**34. `Record Repost Outcome`** (Code) - both branches land here and produce one shape: `repost_attempted`, `repost_skip_reason`, `repost_delivered`, `repost_channel`, `repost_warnings`.

### Group: Shared Write Path - Table, CRM, Reply

**35. `Apply Encounter Update`** - Data Table update on `oak_encounters` with the pre-merged `eu` values. Soft-fails.

**36. `Summarize Action Result`** (Code) - checks every side effect and appends honest notes to both the reply and the CRM note: task not created, no owner email, no Slack user for that email, DM refused ("The bot needs users:read.email and im:write"), card not reposted and why, encounter not stored, or no contact id.

**37. `CRM Writable?`** (IF, `contact_id` not empty)
- True: `Has Properties?`.
- False: skip HubSpot, go to `Reply Action Result`.

**38. `Has Properties?`** (IF, patch has at least one key)
- True: `Patch Action Properties`, then `Create Action Note`.
- False: straight to `Create Action Note`. Snooze, Send to owner and Add note change no property, and HubSpot rejects a PATCH with an empty `properties` object.

**39. `Patch Action Properties`** - PATCH only this action's `oak_*` fields, plus `hs_lead_status` or `lifecyclestage` where the action is a real pipeline event.

**40. `Create Action Note`** - attributed HubSpot note associated to the contact (type 202). Every shared-path action leaves one, so the booth history survives outside Slack.

**41. `Reply Action Result`** (Slack) - the thread reply, for example "Promoted to *Tier 1* by @rep and reposted to the hot channel. Reason: confirmed renewal."

### Group: Mark the Card Handled

**42. `Mark Card Handled`** (Slack reaction `white_check_mark`) - reached from both the claim path and the shared write path, so anyone scrolling the channel sees the lead is handled. A second press returns `already_reacted`, which is soft-failed.

## Decisions worth defending

1. **Verify before reading anything, fail closed.** It is a public webhook that writes to the CRM. No secret means 503 and nothing runs; a bad or stale signature means 401.
2. **Acknowledge first, work second.** Slack's 3-second window and the 3-second `trigger_id` for modals both demand it.
3. **Claim is a conditional update plus a read-back.** One owner, and an honest "already claimed by" for the second rep.
4. **Ten buttons, one write path.** Planned in `Plan Action Effects`, written once, so row, CRM patch, note and reply cannot drift apart per button.
5. **Reclassification replays the stored decision through 03.** One card builder; the new card gets the right channel and buttons. The version this replaced built the card from the clicked message's blocks, which are empty on a modal submission, so a promote posted a heading and no lead.
6. **Reasons are asked for where they matter.** Promote and Not a fit are extremes and need a modal reason; Matched and False alarm are one tap, because charging a form for the middle outcome is how reps stop correcting.

## Likely interview questions

**How do you know a request really came from Slack?** Slack signs `v0:{timestamp}:{raw body}` with the app's signing secret. `Verify Signature and Parse` recomputes the HMAC-SHA256 over the raw bytes, compares with `timingSafeEqual`, and rejects timestamps older than 300 seconds to stop replays. If `OAK_SLACK_SIGNING_SECRET` is not set it returns 503 and nothing runs.

**Two reps hit Claim at the same second. What happens?** `Claim Encounter If Unowned` updates the row only where `claimed_by_slack_id` is empty, so only one update can land. `Reload Claim State` reads the winner back and `Build Claim Result` compares it with the clicker. The winner gets the card update and CRM write; the other rep gets "Already claimed by {name}" and writes nothing.

**Walk me through Promote to Tier 1 on a review card.** The tap opens a modal asking why. The submission comes back into the same webhook, is verified, and routes to effects. The planner sets `override_classification=tier_1` and `oak_lead_tier=tier_1`; the repost branch loads `raw_json` for that scan from `oak_deliveries`, sets the new tier, drops `routing` and calls 03, which posts a fresh card in `#booth-hot` with Tier 1 buttons and the banner "Moved from Needs review to Tier 1 by @rep - confirmed renewal". Then the row update, HubSpot patch, note, thread reply and check mark.

**Why does "Why this score?" not call the AI?** An explanation must never be able to change what it explains, and it must match what was decided at scan time. It reads the stored rule, score breakdown and reasons from `oak_encounters`. It is instant and free.

**What if the HubSpot contact does not exist?** Claim only appears when there is a contact id. On the shared path, `CRM Writable?` skips HubSpot, the encounter row is still updated, and the reply says "No HubSpot contact id was on the card, so no CRM write was attempted."

**How does a rep's Not a fit survive a re-scan?** It is stored as `override_classification=out_of_scope` on the encounter (Promote, Matched and False alarm store their tier the same way). On the next scan 01's `Build Visitor Context` reads it back as `rep_override`, and 01's final gate (or 04, when 05 is skipped) honours it, unless HubSpot or the visitor, at 0.9 confidence or higher, now puts the company in the 1,000+ band. Then it goes to review and "Neither side wins silently".

## The card shows who did what (added)

- **Claim:** the card title gets " — <name> is on it", the "Claimed by" line appears under it, the Claim button goes, and the card gets a 👀 reaction: someone is working on it.
- **Every other action:** after the thread reply, the card title gets " — <name> <what they did>", for example "✅ MATCHED · Dana Cohen - confirm company size — Noam Lev marked it not a fit", and the card gets a ✅ reaction. A later action replaces the earlier ending, so the title always shows the latest.
- **Form actions (Not a fit, Promote, Meeting booked, Add note):** Slack does not send the card with a form submission, and the app has no permission to read channel history. So `Remember Card` saves the card in `oak_encounters` (`card_blocks_json`, `card_blocks_ts`) when the form opens, and the submission uses it only when the message timestamp matches. If it is missing, the title is left alone; the thread reply and the ✅ still happen.
- The name is the claimant's real name when the same person acts, otherwise their Slack username.
- **Status line under the buttons**, next to the reactions: "👀 *Noam Lev* is on it · 19:40 UTC" after a claim, then "✅ *Noam Lev* marked it not a fit · 19:52 UTC" after the next action. Only the latest line is kept.
- **HubSpot `oak_unqualified_reason`:** Not a fit writes the rep's reason (choice plus free text, up to 500 characters) to this contact property, next to Lead status = Unqualified. Promote to Tier 1 and Matched clear it, so it never contradicts the current tier. The property must exist in HubSpot: the Oak properties go out in one update, and HubSpot rejects the whole update if a field is unknown.
