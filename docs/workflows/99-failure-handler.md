# 99 - Failure Handler

Workflow id `N4JW7CO6qjdLi6mq` · 3 working nodes plus a sticky · read from the deployed workflow on 2026-09-28.

## In one breath

99 is the shared error workflow: nothing calls it directly, but the eight other active Oak workflows (01, 02, 02A, 03, 04, 05, 06, 07) name it in `settings.errorWorkflow`, so n8n runs it whenever one of them fails in production. It turns a raw stack trace into a plain-language Slack post in `#oak-revops`: which stage broke, what that costs the event, the likely cause, and a link to the failed execution.

## Trigger, input, output

| | |
|---|---|
| Trigger | `When an Oak Workflow Fails` (Error Trigger). Production executions only; a manual test run never reaches it. |
| Input | n8n's error payload: `workflow.name`, `workflow.id`, `execution.id`, `execution.url`, `execution.error.message`, `execution.error.node.name`, `execution.lastNodeExecuted`. |
| Output | One Slack message in `#oak-revops`, plus a structured item (`stage`, `failed_node`, `error_message`, `execution_url`, `likely_cause`, `reported_at`) visible in the execution. |

## Step by step

There are no node groups apart from the interview guide sticky.

**1. `When an Oak Workflow Fails`** (Error Trigger) - fires with the failure payload from whichever workflow named 99 as its error workflow.

**2. `Explain the Failure`** (Code) - four small decisions:
- **Which stage.** It parses the stage number out of the workflow name, after "GTM", rather than matching the full name:
```js
const stageKey=(function(){const after=String(name).replace(/^[\s\S]*?GTM/i,'');const m=/(\d{1,2}A?)/i.exec(after);return m?m[1].toUpperCase():'';})();
```
  "Oak GTM - 02A CRM Context [sub-flow]" gives `02A`. An earlier version keyed on the literal full name, and every rename silently degraded alerts to a generic message.
- **What it costs.** A lookup of business impact per stage, written for a GTM person on a show floor, for example:
  - 01: "A booth scan may not have been recorded at all. Re-send the same webhook payload ... replaying an incomplete scan is safe."
  - 03: "HubSpot is up to date but nobody was notified. Tell the booth manually, and treat this as urgent while the event is live."
  - 04: only the company intel is missing. 05: rules still decided the tier; the question fell back to a generic one. 06: a button did not take effect; ask the rep to try again.
  - An unknown stage still alerts with a generic impact line and the execution link. A lookup miss must never swallow an alert.
- **Likely cause.** Regex on the error message: 401/403/unauthorized/credential means a credential problem; 429/rate limit means rate limiting; timeout/ETIMEDOUT/ECONNRESET/ENOTFOUND means network; property/does not exist/invalid means a schema mismatch ("rerun workflow 00").
- **The message.** ":rotating_light: Oak GTM failure - {stage}", workflow, failed node, error (truncated to 600 chars), "What this means", "Likely cause", and a link "Open the failed execution". Whole text capped at 3,500 characters.

**3. `Report to RevOps`** (Slack, channel `oak-revops` by name, OAuth2 credential) - retries 3 times, 2 seconds apart, and deliberately has **no** soft-fail. If Slack still refuses, this execution fails, so a lost alert stays findable in the execution list instead of being reported as a success.

Precise if asked: the node note says it posts to the channel the policy declares as `notifications.ops_alerts`. The policy does declare `oak-revops` there, but 99 does not read the policy; the channel name is typed into the node. The value matches, but changing `ops_alerts` in the table would not move failure alerts. It also uses the OAuth2 Slack credential ("Slack account"), not the Slack app credential 03 and 06 use, which is fine because 99 posts no buttons.

## Decisions worth defending

1. **Written for the reader on the floor, not an engineer.** "HubSpot is stale, look the contact up by email" beats a stack trace.
2. **Impact differs by stage, so the message does.** Intake failing can lose a scan; enrichment failing only thins the card.
3. **Key on the stage number, not the name.** Renames cannot break the mapping.
4. **Retries and soft-fails live on the watched workflows' nodes.** 99 is for what survives them.
5. **The alert itself must not fail silently.** No `continueOnFail` on the Slack node.

## Likely interview questions

**How does a workflow failure reach Slack?** Each Oak workflow sets `errorWorkflow` to 99. n8n runs 99's Error Trigger with the workflow name, failed node, error and execution URL; `Explain the Failure` maps it to a stage, impact and likely cause; `Report to RevOps` posts it to `#oak-revops` with a link to the execution.

**Why not just forward the error message?** The person reading at an event needs to know what it costs and what to do now. The stage-specific impact line answers that, for example that a 03 failure means nobody was told about a lead and someone should tell the booth manually.

**What if the failure handler itself fails?** The Slack node retries three times and then fails the execution, so it shows as an error in the execution list. n8n does not run an error workflow for its own error workflow, to avoid a loop, so the 99 impact line is defensive.

**Why is there a 07 entry if 07 is a manual test harness?** Error workflows fire only for production executions and 07 is run by hand, so today it never fires. It stays mapped so a rename, or 07 gaining a real trigger, cannot quietly produce a generic alert.

**Where do soft-fails end and alerts begin?** Most external calls in 01 to 06 retry and continue on error, recording warnings in the payload, so the lead still flows. 99 only sees what escapes those: an unhandled exception, or a node deliberately left hard-failing.
