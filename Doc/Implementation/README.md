# Implementation — MobiSentra AI

Execution runbooks for building MobiSentra AI, in strict sequence.

## Documents

| Doc | Role |
|---|---|
| [`implementation-sequence.md`](./implementation-sequence.md) | **THE runbook** — every step in execution order, with commands, files, and gates. Start here. |
| [`phase-2-completion.md`](./phase-2-completion.md) | Phase 2 completion report — gate evidence, A/B tables, pending owner checklist/decision |
| [`phase-2-plan.md`](./phase-2-plan.md) | Phase 2 (detection + tracking) design plan — approved & executed |
| [`phase-1-plan.md`](./phase-1-plan.md) | Phase 1 (video ingestion) design plan — approved & executed |
| [`phase-1-completion.md`](./phase-1-completion.md) | Phase 1 completion report — evidence, issues hit |
| [`phase-0-completion.md`](./phase-0-completion.md) | Phase 0 completion report — evidence, corrections, issues hit (2026-08-25) |
| [`../plan.md`](../plan.md) | Original vision / architecture background — *why the project exists* |
| [`../implementation-plan.md`](../implementation-plan.md) | Locked decisions, rationale, risk register, timeline — *what we build & why* |
| [`../handoff-2026-08-24.md`](../handoff-2026-08-24.md) | Session log from the planning session (2026-08-24) |

## How to use

1. Open `implementation-sequence.md`.
2. Work steps **top-to-bottom**. Never start a phase before the previous **GATE** passes.
3. Tick the `- [ ]` checkboxes as you complete steps; update the **Progress Tracker** at the top of the runbook.
4. If a step ever contradicts `implementation-plan.md`, the plan wins — fix the sequence doc, not the plan.

### Per-phase working agreement (owner directive, 2026-08-25)

For every phase: **(1)** write the phase plan doc first (`phase-N-plan.md`),
**(2)** owner approves it, **(3)** divide the plan into its scheduled steps and
execute them **one by one** in order — no batching ahead, no skipping — with
each step verifiable before moving to the next. Completion report
(`phase-N-completion.md`) closes the phase with gate evidence.

## Rules baked into every step

- **Owner runs 100% of git operations** (init, commit, push, tag, PR). The agent edits files only and reports a suggested commit message.
- **Runs-without-hardware:** bundled sample videos are the default input. Real RTSP is a config change, never a requirement.
- **Every phase ends with a measurable GATE.** No gate → no next phase.
- **Sample-data-first:** only open datasets (UR Fall, Le2i, Hockey Fights, UBI-Fight). RWF-2000 is never bundled (non-commercial license).
