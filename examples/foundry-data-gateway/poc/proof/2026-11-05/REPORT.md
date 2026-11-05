# foundry-data-gateway PoC: end-to-end proof

Run: 2026-11-05 11:25:40 UTC. Model: deterministic stub (no provider, no spend). Data: synthetic, Alphazed-shaped.

## Checks

| Check | Result |
|---|---|
| amal: struggling_learners answered from real rows | PASS |
| amal: every row is an amal learner (row-level security) | PASS |
| amal: subject_progress answered | PASS |
| noor: struggling_learners refused by policy (tool_not_granted) | PASS |
| noor: progress shows only noor learners | PASS |
| invented tool refused unsent (no receipt, no webhook) | PASS |
| tool results reached the model fenced as untrusted data | PASS |
| db: alphazed_reader cannot select parent_email | PASS |
| db: no org set -> zero learner rows | PASS |
| control: RLS off -> same call returns amal learners | PASS |
| control: RLS back on -> noor only again | PASS |
| mcp: noor lists only its 2 granted tools | PASS |
| mcp: tools/call list_subjects(5-6) returns 2 subjects | PASS |
| webhook: forged signature -> 401, nothing read | PASS |
| receipts: allow + refuse both recorded | PASS |
| receipts: no receipt for the forged call | PASS |
| receipts API is org-scoped | PASS |

## Conversation turns

**01-amal-struggling** — teacher asks: _Which learners are struggling and need help?_

> 7 learner-subject pairs need help, weakest first: Yousef in Arabic Letters (avg 33.3); Yousef in Reading Short Stories (avg 38.3); Yousef in Adding Numbers (avg 40.5); Maryam in Arabic Letters (avg 43.8); Maryam in Counting 1-10 (avg 56.3); Maryam in Reading Short Stories (avg 56.5); Lina in First Arabic Words (avg 59.7).

events: `turn.started → usage → artifact → text.delta ×52 → usage → turn.done`

**02-amal-progress** — teacher asks: _Show progress in arabic-letters_

> Progress for 4 learners: Adam: 2 bytes done, avg 75.0; Lina: 3 bytes done, avg 67.0; Maryam: 1 bytes done, avg 43.8; Yousef: 0 bytes done, avg 33.3.

events: `turn.started → usage → artifact → text.delta ×28 → usage → turn.done`

**03-noor-not-granted** — teacher asks: _Which learners need help?_

> I can't answer that from the school's data: struggling_learners is not granted to this organisation (rule: tool_not_granted).

events: `turn.started → usage → artifact → text.delta ×17 → usage → turn.done`

**04-noor-progress** — teacher asks: _Show progress in arabic-letters_

> Progress for 3 learners: Huda: 3 bytes done, avg 85.0; Rania: 3 bytes done, avg 67.7; Zaid: 1 bytes done, avg 46.0.

events: `turn.started → usage → artifact → text.delta ×22 → usage → turn.done`

**05-invented-tool** — teacher asks: _Give me the parents' email addresses_

> I can't answer that from the school's data: unknown tool 'learner_emails' (rule: n/a).

events: `turn.started → usage → text.delta ×13 → usage → turn.done`

## Receipts (data_tool_receipts)

| channel | org | subject | tool | decision | rule | rows |
|---|---|---|---|---|---|---|
| webhook | school-amal | teacher-amal | struggling_learners | allow | granted | 7 |
| webhook | school-amal | teacher-amal | subject_progress | allow | granted | 4 |
| webhook | school-noor | teacher-noor | struggling_learners | refuse | tool_not_granted | 0 |
| webhook | school-noor | teacher-noor | subject_progress | allow | granted | 3 |
| mcp | school-noor | control | subject_progress | allow | granted | 7 |
| mcp | school-noor | control | subject_progress | allow | granted | 3 |
| mcp | school-noor | mcp-client | list_subjects | allow | granted | 2 |
