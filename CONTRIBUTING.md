# Contributing to MobiSentra AI

Thanks for contributing! This is a safety-focused open-source project — a few ground rules keep it healthy.

## Core principles

1. **Runs without hardware.** The default demo input is bundled sample videos + open datasets. Nothing you add may require a camera, RTSP stream, or Jetson to run. Real CCTV is a config change.
2. **Sample-data-first.** Only open datasets (UR Fall, Le2i, Hockey Fights, UBI-Fight). **Never** add RWF-2000 or any non-commercial-restricted dataset.
3. **Open stack only.** Every component in the default compose file must be OSI-open licensed (no source-available defaults). Spot-check the license of any new dependency and add it to the dependency notes.
4. **Safety events, not identities.** No facial recognition, re-identification, or demographic inference — in code, not just docs.
5. **Gates are hard.** Each phase in [`Doc/Implementation/implementation-sequence.md`](Doc/Implementation/implementation-sequence.md) ends with a measurable gate. Don't weaken a gate to pass it.

## Development environment

- **Python (edge/):** [uv](https://docs.astral.sh/uv/) — `cd edge && uv sync`
- **Node (backend/):** pnpm — `cd backend && pnpm install`
- **Infra:** Docker — `docker compose -f infra/docker-compose.yml up -d`

Run checks before opening a PR:

```bash
cd edge && uv run ruff check . && uv run pytest
cd ../backend && pnpm typecheck && pnpm test
```

CI runs the same commands on every push and PR.

## Pull requests

- Keep PRs focused — one logical change per PR.
- New behavior needs a test (the event engine's golden-file suite is the highest-value test surface in the repo).
- Update `CHANGELOG.md` under `[Unreleased]`.
- Performance-sensitive vision changes: include FPS numbers on the sample footage.

## Commit style

Conventional commits (`feat:`, `fix:`, `docs:`, `ci:`, `refactor:` …).

## Reporting issues

Use the issue templates. For security-sensitive findings, prefer a private disclosure over a public issue.

## Code of conduct

By participating you agree to uphold the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
