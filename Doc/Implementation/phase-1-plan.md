# Phase 1 — Video Ingestion · Plan

> **Status: APPROVED & EXECUTED 2026-08-25 — see [`phase-1-completion.md`](./phase-1-completion.md) for results (Gate 1 PASSED)**
> Source of truth: [`implementation-sequence.md`](./implementation-sequence.md) → Phase 1 (on conflict, runbook + `implementation-plan.md` win)
> Duration: ~1 week · Budget: 6 build days + 1 soak/doc day
> Prerequisite: Gate 0 ✅ (2026-08-25)

---

## 1. Objective

Deliver the frame supply every later phase depends on:

> **Lag-free frames from any source; the pipeline never crashes on stream loss.**

The #1 real-world pitfall this phase kills: naive `cap.read()` in the main loop accumulates a 2–3 minute backlog because OpenCV buffers frames faster than inference consumes them. By Phase 2 (YOLO) the video would be minutes behind reality.

## 2. Gate 1 (unchanged from runbook)

| # | Criterion | Measured by |
|---|---|---|
| 1 | 60+ min run with **< 200 ms** sustained lag | soak script metrics (§7) |
| 2 | Camera kill → restore: **auto-recovers < 15 s**, process never crashes | MediaMTX fault-injection test |
| 3 | All ingestion unit tests green | `uv run pytest` |

Lag definition (v1): `consume_time − capture_time`, where `capture_time` is stamped by the reader thread at `cap.read()` return. Limitation documented: not camera-side timestamping.

## 3. Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Threading | **One reader daemon thread per camera**; consumer in main loop | Threads block in FFmpeg C code (GIL released); MVP scale is 1–6 cameras. Multiprocess escape hatch noted, not built |
| Frame policy | **Latest-frame-only slot** (lock-guarded overwrite) | Consumer always sees the freshest frame; stale frames are dropped, never queued. Proves lag can't accumulate |
| Capture ownership | Only the reader thread touches `cv2.VideoCapture` | OpenCV captures are not thread-safe; design enforces it |
| Sample playback | File sources are **paced to native FPS** (monotonic target-time scheduling, not naive sleep) | A file read at full speed isn't a live camera; pacing makes lag/FPS metrics meaningful |
| EOF behavior | Sample files **loop** (seek 0, keep counting frame_index) | Emulates continuous camera; no manual restart |
| Testability | StreamReader takes injectable **capture factory + clock** | Unit tests use fakes — no real camera/RTSP needed for watchdog/backoff/state tests |
| Backoff | Exponential 1→2→4→…→60 s cap; watchdog triggers at **10 s** without a frame | Matches runbook contract |
| RTSP transport | Force TCP via `OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp"` | Avoids UDP packet-loss artifacts/corrupt frames on flaky networks |
| Throttling location | `analyze_every_n_frames` is **consumer-side** | Reader must drain the buffer regardless; skipping belongs in the consumer loop |
| GUI | Preview window is an **opt-in `--preview` flag** | Tests and CI are headless; never open windows by default |

## 4. Source Scheme (extends Phase 0 camera registry)

| Config `source:` | Resolves to | Behavior |
|---|---|---|
| `sample://videos/<name>.mp4` | `edge/sample_data/videos/<name>.mp4` | paced loop, **default** |
| `file:///abs/path.mp4` | that path | paced loop |
| `0` (integer) | webcam | live, no pacing |
| `rtsp://…` | FFmpeg capture + TCP | live, no pacing |

GStreamer/Jetson NVDEC variant: config key stub only (documented, not implemented — Phase 10).

## 5. Module Plan

```
edge/mobisentra/
├── ingestion/
│   ├── sources.py        # source string → capture arg resolution (+ validation errors)
│   ├── frame.py          # Frame dataclass: image, capture_ts, frame_index, source_id
│   └── stream_reader.py  # StreamReader, StreamStatus (UP/DOWN/RECONNECTING)
├── metrics.py            # JSONL per-minute metrics writer (runs/, gitignored)
└── main.py               # Phase 1 orchestrator: N readers → consumer loops → metrics/preview
edge/tests/
├── conftest.py           # synthetic-video fixtures (cv2.VideoWriter, frame index drawn in)
├── test_sources.py       # resolver table + error cases
├── test_stream_reader.py # ordering, latest-only, EOF loop, watchdog, backoff (fakes + fake clock)
└── soak.py               # manual 60-min soak script; results → Doc/
edge/sample_data/videos/  # 2–3 open-licensed clips + SOURCES.md (origin + license each)
                          # + 1 tiny generated synthetic clip for deterministic tests
```

### StreamReader contract (as in runbook, plus details)

```python
class StreamReader:
    def __init__(self, camera: CameraConfig, *, open_capture=None, clock=None,
                 watchdog_timeout_s: float = 10.0): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...            # idempotent, joins thread < 5 s
    def get_frame(self, timeout_s: float | None = None) -> Frame | None: ...
    def status(self) -> StreamStatus:      # UP | DOWN | RECONNECTING, last_frame_ts, reconnects
```

## 6. Test Plan

**Unit (fakes, no hardware):**
1. Resolver: each source scheme + malformed strings raise `ConfigError`
2. Frames returned in order (synthetic fixture with drawn numbers)
3. **Latest-only:** slow consumer (sleep between gets) receives monotonically recent frames — never a stale queue
4. EOF loop: sample file restarts; frame_index monotonic; pacing keeps ~native FPS
5. Watchdog: capture that stops producing → status DOWN within watchdog timeout (fake clock)
6. Backoff sequence: 1,2,4,…,60 cap; recovery resets to 1 (fake clock)
7. stop() is idempotent; reader thread exits cleanly

**Integration (local, manual/automated where possible):**
8. Webcam quick check (optional — macOS permission prompt)
9. **RTSP fault injection via MediaMTX** (MIT-licensed RTSP server, docker): serve a sample clip as RTSP → stop container (= camera killed) → start (= restored) → assert DOWN detected ≤ 10 s, recovered ≤ 15 s, process alive

**Soak (manual, gate evidence):**
10. 60 min on sample + RTSP-via-MediaMTX; per-minute JSONL: read FPS, consumed FPS, lag ms (p50/p95/max), reconnects, drops

## 7. Soak & Evidence

- `uv run python tests/soak.py --config configs/cameras.yaml --minutes 60` → writes `runs/soak-<date>.jsonl` + prints summary table
- Summary transcribed into `Doc/Implementation/phase-1-completion.md` (same format as Phase 0 report)
- MediaMTX added to compose as an **opt-in profile** (`--profile rtsp`), not default — keeps `docker compose up` lean

## 8. Sample Video Acquisition

- 2–3 clips from open repositories (Pexels/Coverr-style permissive licenses): bus/station/crowd + one low-light
- `SOURCES.md` records origin URL + license per clip (project rule)
- 1 generated synthetic clip (counting frames on plain background) committed for deterministic tests — tiny, no license concerns

## 9. Schedule

| Day | Deliverable |
|---|---|
| 1 | `sources.py` + `frame.py` + fixtures + `test_sources.py` green |
| 2 | StreamReader core (thread, latest-only slot) + tests 2–4, 7 green |
| 3 | Watchdog + backoff + tests 5–6 green; ruff clean |
| 4 | `main.py` orchestrator + metrics + `--preview`; sample clips + SOURCES.md |
| 5 | MediaMTX profile + fault-injection test (9) passing |
| 6 | 60-min soak; numbers recorded |
| 7 | Buffer: fixes, `phase-1-completion.md`, gate review with owner |

## 10. Risks

| Risk | Mitigation |
|---|---|
| GIL contention at 4–6 cameras | Threads are I/O-blocked in C; benchmark in soak; escape hatch = process-per-camera (documented, not built) |
| File pacing drift | Schedule by absolute target time (`t0 + n/fps`), not relative sleeps |
| RTSP servers rejecting TCP / odd codecs | Test against MediaMTX first; codec note (H.264) in infra README |
| macOS webcam permission blocks CI | Webcam tests optional/skipped by default |
| Big clips bloat repo | Keep committed clips ≤ ~5 MB, generated synthetic ≤ 1 MB; downloads scripted, not committed |

## 11. Out of Scope (explicit)

- GStreamer/NVDEC pipeline (Phase 10 stub only)
- Detection/tracking/pose (Phase 2)
- Evidence buffers (Phase 4)
- Camera-side timestamp sync (RTSP NTP) — v1 limitation documented
- Multi-process reader pool

## 12. Approval

- [ ] Owner reviews this plan
- [ ] → On approval: implementation starts at Day 1 (`sources.py`)
