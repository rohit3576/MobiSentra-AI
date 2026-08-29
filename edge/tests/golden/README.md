# Golden files — event engine contracts (Phase 6, Step 6.4)

Each `*.json` here is a **byte-exact behavior contract** for the event
engine: a scripted frame sequence in, the exact CloudEvents envelope stream
out, plus the expected suppression counts. `test_golden.py` replays every
scenario through the **real** resolver + engine with a fixed id-factory
(envelope `id`s are `gold-001`, `gold-002`, …; times derive from row `ts`),
compares the emitted stream to `expected_envelopes` exactly, and validates
every expected envelope against the shared `schemas/events/v0/`.

## Why the policy is inline (not `configs/severity.yaml`)

Goldens lock *engine behavior*, not operator taste. The scenario policy
(cooldowns, escalation window, severities) is frozen inside each golden so
editing `configs/severity.yaml` never breaks them. The resolver **rules**
(into-`overcrowded` → `overcrowding` kind, ratio escalation) are exercised
for real — a rule change in code shows up as a golden diff, which is the
point.

## Regenerating (deliberate contract changes only)

1. Change the engine/resolver behavior intentionally.
2. `GOLDEN_REGEN=1 .venv/bin/python -m pytest tests/test_golden.py`
   rewrites every `expected_envelopes`/`expected_suppressed` block.
3. **Review the diff line by line** — regeneration blesses whatever the
   code now does; the diff is the review artifact. A surprising diff means
   a bug, not a golden to regenerate.

## Scenario map

| File | Locks |
|---|---|
| `repeated-falls.json` | one alert per track per cooldown; suppression counting; re-arm after cooldown expiry |
| `occupancy-flicker.json` | band-escalation emission (incl. ratio → HIGH), duplicate-transition suppression, de-escalation exempt + re-arm, family-key pairing |
| `fight-below-fusion.json` | upstream silence preserved: non-fight rows never produce `altercation_suspected` envelopes |
| `mixed-scenario.json` | cross-kind interplay: fall + fight re-fire → CRITICAL escalation + zone entry + occupancy bands + fall re-arm, no cross-kind interference |
