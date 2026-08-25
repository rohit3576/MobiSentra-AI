# Changelog

All notable changes to MobiSentra AI are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Phase 1: Video Ingestion

- `StreamReader` (latest-frame-only reader thread per camera): lag cannot
  accumulate; paced file/sample playback at native FPS; EOF loop; live-source
  reconnect with exponential backoff (1→60 s cap).
- Source resolution: `sample://` (default, bundled clips), `file://`, webcam
  index, `rtsp://` with forced TCP + open/read timeouts.
- Orchestrator (`mobisentra.main`) with per-minute JSONL metrics, end-of-run
  summary, `--preview`, `--rtsp` extras, graceful SIGINT/SIGTERM.
- Soak runner (`edge/tests/soak.py`) — gate evidence tool.
- RTSP fault-injection test (camera kill/restore) + `rtsp` compose profile:
  MediaMTX + ffmpeg fake camera serving a bundled clip as a real RTSP stream.
- Synthetic sample clips (day/night/dusk) + generator tool + `SOURCES.md`.
- 22 new tests: fakes-based (ordering, latest-only, EOF loop, pacing,
  backoff, death/reconnect, stop) + real-file roundtrip + resolver table.

### Changed

- `edge/sample_data/videos/` whitelisted in `.gitignore` (bundled clips ship
  with the repo; `runs/` stays local).

### Added — Phase 0: Scaffold & Environment

- Monorepo layout: `edge/` (Python vision pipeline), `backend/` (Node.js + TS), `dashboard/`, `bridge/`, `mlops/`, `infra/`, `schemas/`.
- Dev stack via `infra/docker-compose.yml`: Apache Kafka 4 (KRaft), EMQX 5.10 (MQTT), PostgreSQL 16, Redis 7, MLflow, MQTT→Kafka gateway.
- **MQTT→Kafka gateway service** (`bridge/`): Node + `@confluentinc/kafka-javascript`. EMQX open-source ships no Kafka connector (Enterprise-only; verified against 5.8.3 and 5.10.3), so the gateway keeps the default stack fully OSI-open. Topic mapping: MQTT `mobisentra/events` → Kafka `mobisentra.events`. Smoke-tested end-to-end (containerized round-trip verified).
- Event schema v0 frozen: CloudEvents 1.0 envelope + per-type JSON Schemas (`schemas/events/v0/`), validated by test suites in both `edge` and `backend`.
- Camera registry format (YAML) + validated loader (`edge/mobisentra/ingestion/config.py`).
- CI: lint + tests for Python and TypeScript on every push (`.github/workflows/ci.yml`).
- Project hygiene: AGPL-3.0 LICENSE, README, CONTRIBUTING, Code of Conduct, issue templates.
