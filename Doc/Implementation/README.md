# Implementation — MobiSentra AI

Execution runbooks for building MobiSentra AI, in strict sequence.

## Documents

| Doc | Role |
|---|---|
| [`implementation-sequence.md`](./implementation-sequence.md) | **THE runbook** — every step in execution order, with commands, files, and gates. Start here. |
| [`phase-4-plan.md`](./phase-4-plan.md) | Phase 4 (fall detection) design plan — 4.1–4.5 executed (evidence clips, UR Fall benchmark: 93.3% detection, FP hard-negative analysis, gate verdict + owner decision pending); 4.6 fold remains |
| [`phase-8-plan.md`](./phase-8-plan.md) | Phase 8 (backend services) design plan — **APPROVED 2026-08-29, executing one-by-one**; 8.1b + 8.1a + 8.2 + 8.3a + **8.3c ✅** (consumer wrapper, typed records, Redis dedupe fail-open, PG migrations live-applied, Socket.IO vehicle rooms; backend 45/45 + tsc clean); remaining 8.3b, 8.4a, 8.5, 8.4b |
| [`phase-9-plan.md`](./phase-9-plan.md) | Phase 9 (dashboard → **v0.1.0**) design plan — **DRAFT, awaiting approval**; planned ahead of Gate 8 on purpose: carries two Phase-8 amendments (8.3b analytics branch for occupancy, 8.4a evidence route) that must be approved together so the backend lands once; 9.1a–9.7 division + defaults table |
| [`phase-7-completion.md`](./phase-7-completion.md) | Phase 7 completion report — **closed 2026-08-29, Gate 7 PASSED 3/3** (10-min blackout soak: 144/144 zero-loss, dupes collapsed by id-dedupe, kafka kill/restore clean; edge 369+4, bridge 15/15) || [`phase-7-plan.md`](./phase-7-plan.md) | Phase 7 (edge messaging: spool → MQTT → bridge → Kafka) design plan — **EXECUTED + CLOSED 2026-08-29** (7.1a–7.4b all ✅ one day; two bridge flaws found live & fixed; lossless backpressure via persistent-session disconnect) |
| [`phase-6-completion.md`](./phase-6-completion.md) | Phase 6 completion report — **closed 2026-08-29, Gate 6 PASSED 3/3** (goldens byte-exact + reviewed, purity guard, all outputs schema-valid incl. production smoke 55/55; suite 327+4) |
| [`phase-6-plan.md`](./phase-6-plan.md) | Phase 6 (event engine + severity) design plan — **EXECUTED + CLOSED 2026-08-29** (6.1a–6.4 all ✅ one day, zero owner-input steps; two in-loop bugs caught by tests; fight production enablement parked as owner decision) |
| [`phase-5-completion.md`](./phase-5-completion.md) | Phase 5 completion report — **closed 2026-08-29 by owner decision (c)**: fight path built/wired/tested (235+4 suite, 6× realtime), Gate 5 numeric criteria unmet and accepted as documented limitation; tuning deferred to Phase 10 |
| [`phase-5-plan.md`](./phase-5-plan.md) | Phase 5 (altercation) design plan — EXECUTED + CLOSED 2026-08-28/29 (5.1–5.5 all ✅; ONNX runtime pick, UBI/Hockey sourced creds-free, windowed protocol; honest baseline recorded) |
| [`phase-3-completion.md`](./phase-3-completion.md) | Phase 3 completion report — Gate 3 at 2/3 (empty-zone FP ✅ 30 min/0 events; occupancy verdict awaits owner's 5 manual counts) |
| [`phase-3-plan.md`](./phase-3-plan.md) | Phase 3 (zones/occupancy/door) design plan — **DRAFT, awaiting approval** |
| [`phase-2-completion.md`](./phase-2-completion.md) | Phase 2 completion report — gate evidence, A/B tables, pending owner checklist/decision |
| [`phase-2-plan.md`](./phase-2-plan.md) | Phase 2 (detection + tracking) design plan — approved & executed |
| [`phase-1-plan.md`](./phase-1-plan.md) | Phase 1 (video ingestion) design plan — approved & executed |
| [`phase-1-completion.md`](./phase-1-completion.md) | Phase 1 completion report — evidence, issues hit |
| [`phase-0-completion.md`](./phase-0-completion.md) | Phase 0 completion report — evidence, corrections, issues hit (2026-08-25) |
| [`../plan.md`](../plan.md) | Original vision / architecture background — *why the project exists* |
| [`../research/bus-reality.md`](../research/bus-reality.md) | Web research 2026-08-28 — real-world bus incident patterns (India + global), validates the MVP event set, sources the post-MVP backlog additions (footboard detection, panic-button ingestion, harsh-braking correlation) |
| [`../implementation-plan.md`](../implementation-plan.md) | Locked decisions, rationale, risk register, timeline — *what we build & why* |
| [`../handoff-2026-08-29-session2.md`](../handoff-2026-08-29-session2.md) | Session log (same day, second arc): **Phase 8 planned + steps 8.1b/8.1a/8.2 executed** (consumer wrapper, typed records, Redis dedupe; backend 31/31) — pickup list + staged-commit note inside |
| [`../handoff-2026-08-29.md`](../handoff-2026-08-29.md) | Session log: Phase 5 closed; **Phases 6 + 7 planned → executed → gates PASSED in one day** (event engine + golden suite; edge messaging + 10-min-blackout soak) — pickup list + uncommitted inventory inside |
| [`../handoff-2026-08-27.md`](../handoff-2026-08-27.md) | Session log: Phases 4.4–4.6 + 3.6 executed (evidence clips, UR Fall 93.3%, REST zones, Gate-3 evidence, Phase-5 plan draft); pickup list inside |
| [`../handoff-2026-08-26-session2.md`](../handoff-2026-08-26-session2.md) | Session log: Gate 2 resolution (ReID rejected, TrackTrack shipped) + sample-clip hunt (2026-08-26) |
| [`../handoff-2026-08-26.md`](../handoff-2026-08-26.md) | Session log from the Phase 2 completion session (2026-08-26) |
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
