# Oak GTM workflow exports

Import order: 00 first, then 02A, 02, 03, 04, 05, 99, then 01, 06, 07 (01 calls the sub-workflows, so they must exist first). See SETUP.md.

Each file is the published (active) version of the workflow, in n8n import format. Credentials appear only as `{id, name}` references and must be re-linked after import. Workflow 00 has no published version by design, so its file is its current version.

| File | Workflow id | Exported version id | Nodes | Active | Purpose |
|---|---|---|---|---|---|
| `00 - Setup & Seed.json` | `PeoS0NF1f7FHzEID` | `b0ae73f6-f46c-46f1-b6fe-9a653fb34534` | 25 | no | One-time setup: creates the Data Tables, seeds the active policy and competitors, provisions HubSpot properties and Slack channels. Draft-only by design (never published); exported from its current version. |
| `01 - Intake & Decision.json` | `9SzVHK4vdv7Z3UMH` | `095cb155-b6db-4ae5-942f-ae66ef0031f3` | 41 | yes | Booth webhook intake: validates, blocks replays, classifies against the active policy, answers with a 202, then runs 02A, 04, 05, 02 and 03. |
| `02 - HubSpot Sync [sub-flow].json` | `WQ2s5hFfpaE8P45o` | `f13df11d-f091-4cc7-893d-7a5e78389e6e` | 16 | yes | Sub-workflow: the only automated HubSpot writer. Upserts contact and company, writes Oak properties, logs the booth meeting and note. |
| `02A - CRM Context [sub-flow].json` | `xnl1bav7McaQIwXC` | `d1d32bc2-67d9-47f4-b1b3-cf1445786fdc` | 7 | yes | Sub-workflow: read-only HubSpot lookup of contact, company, owner and open deals before enrichment and AI. |
| `03 - Slack Alerts [sub-flow].json` | `gyCxnkoGkh3OJpz5` | `d0c9c3c9-cf76-432a-acc4-28301e41d6e0` | 14 | yes | Sub-workflow: picks the Slack channel from the policy, builds the alert card and buttons, threads repeat visits, returns a delivery receipt. |
| `04 - Company & Person Enrichment [sub-flow].json` | `K9MaVwjIoBmZ0oS9` | `8010d822-11e5-467a-bbc9-4cbbd12caedf` | 20 | yes | Sub-workflow: company and person enrichment (homepage read, gated public search) resolving headcount and industry with source URLs. |
| `05 - AI Lead Reasoning [sub-flow].json` | `McoAVyLN6lxqmLeO` | `7a3fb8a9-1600-41e6-9f7c-c07133700121` | 7 | yes | Sub-workflow: bounded Gemini synthesis that resolves ambiguous titles above a confidence floor and writes the opening question. |
| `06 - Slack Actions.json` | `VsKr9HAqOrtOlxZC` | `87d50258-f92d-46e9-97bf-78bf9e8ed6ea` | 45 | yes | Slack interactivity webhook: verifies signatures and handles the twelve rep buttons (claim, reclassify, notes, snooze and more). |
| `07 - Task Test Harness.json` | `nyht2byorvoxFX2u` | `6d8a6cc5-df17-46ba-95c9-9fee127583cf` | 12 | yes | Manual test harness: replays test payloads through the live 01 webhook and scores each scan PASS/FAIL from stored state. |
| `99 - Failure Handler.json` | `N4JW7CO6qjdLi6mq` | `61065589-cbdf-482e-a3a5-15ec6ee7f72c` | 4 | yes | Error workflow for the others: turns a production failure into a plain-language Slack report in #oak-revops. |
