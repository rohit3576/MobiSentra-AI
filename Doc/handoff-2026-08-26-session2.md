HANDOFF CONTEXT — Session Log 2026-08-26 (Session 2: Gate 2 resolution + sample-clip hunt)
======================================================================================

Project: MobiSentra AI
Session: continuation of Doc/handoff-2026-08-26.md (same day, after the Phase 2 commit)

USER REQUESTS (AS-IS)
---------------------
- "ok then i guess i dont have any option ,wait lets 1st check on the web if there might be any solutuon availble for this"
- "try this" (Option C — custom ReID)
- "ok i committed recommend me"
- "sample videos are not the one we wanted change that or search on web give me link i will download and paste in the sample video"
- "what are you doing ?" (mid-probe; explained, offered to continue)
- "ok save the chat i will find later dont burn out more just save the chat we will start later"

WORK COMPLETED THIS SESSION
---------------------------
1. GATE 2 OPTION C — first-party ReID: executed + rejected with evidence.
   - Discovery: installed ultralytics ships dedicated ReID models
     (yolo26{n,s,m,l,x}-reid.onnx) via trackers/utils/reid.py (AutoBackend,
     CoreML EP) — NOT the old cls-features hack.
   - Added dep: onnxruntime + onnx (uv add; in pyproject.toml now).
   - configs/botsort-reid.yaml (kept as evidence, header carries numbers):
     clip01 reid-n 0.715 / reid-s 0.749 vs 0.741 no-reid (wash);
     clip02 reid-s 0.376 / reid-m 0.540 vs 0.593 (REGRESSION on occlusion
     storm); clip03 reid-s 0.451 vs 0.213 (helps, still far below 0.80).
   - Verdict: crop quality at 480p (~20-40px persons) is the binding wall;
     capacity trend (s->m) recovers part but never beats pure motion.

2. WEB RESEARCH (user asked) — librarian + direct search:
   - Dead ends measured: IoU tracklet stitching (+0.000-0.005, redundant with
     track_buffer 90 — surviving fragments are low-IoU by definition); SAHI
     (nothing to recover from native-480p source); ocsort/deepocsort 0.627,
     fasttrack 0.710 (clip01).
   - WIN: TrackTrack (CVPR 2025, ultralytics-native, one-line swap).
     configs/tracktrack-tuned.yaml = stock with thresholds lowered to our
     regime (high 0.25 / low 0.1 / new 0.2, buffer 90, lost_match_thr 0.9,
     tai_thr 0.3). Results vs botsort-tuned (flicker-filtered / reassociations):
       clip01: 0.681/3 vs 0.741/7
       clip02: 0.663/1 vs 0.593/16  <- occlusion storm: 16x fewer fragments
       clip03: 0.469/3 vs 0.213/5   <- 2.2x ratio
     Average 0.604 vs 0.516; total reassociations 7 vs 28.
   - FPS with TrackTrack: 51.7 @720p / 34.6 @1080p (gate >=15, PASS).
     E2E verified 3 cameras, lag p95 <= 48ms.

3. RECOMMENDATION ACCEPTED: tracktrack-tuned shipped as DEFAULT tracker in
   configs/detection.yaml (+ bench.py default + runbook Step 2.2 + completion
   doc §8 resolution note). botsort-tuned.yaml kept as documented fallback.
   Verified: e2e on stock config green, ruff clean, 54 tests pass.

4. COMMITS (owner-run): 8bd93dd (phase-2 main), bb2ce09 (TrackTrack + reid
   evidence + clips + docs). Post-bb2ce09 uncommitted: detection.yaml default
   flip + bench.py + runbook + completion-doc resolution lines (small).

5. SAMPLE-CLIP HUNT (user: "not the ones we wanted — links, I'll download"):
   Built a verification pipeline for candidates (ffmpeg via temp-venv
   imageio-ffmpeg; optical-flow camera-stability probe; detection-density
   probe) — the temp ffvenv still exists at
   /var/folders/.../T/opencode/ffvenv (may be wiped by OS; recreate via
   python3 -m venv + pip install imageio-ffmpeg if needed).
   VERIFIED GOOD: "Bustling Indian Railway Station Platform" (Aamir Somewhere,
   pexels.com/video/bustling-indian-railway-station-platform-scene-35333118/)
   — tripod, 6.9 people/frame, 16s @960x540x60. Best Indian-context crowd.
   DOWNLOADED, UNVERIFIED (user aborted the probe run; files in T/opencode,
   may be gone): cand_delhi (Delhi metro station, Samar L.,
   pexels 36535410), cand_mumbailocal (Mumbai local at suburban station,
   aksinfo7 universe, pexels 30381290), cand_chrorgate (Mumbai railway
   station interior, Swapnil Shiwalay, pexels 35741023).
   REJECTED during verification: "Crowded Subway Commute Scene" (2.5/frame),
   "People inside the Moving Bus" (0.0/frame), Wikimedia handheld clips
   (earlier: Gangasagar food queue, Tarragona voters — flow p50 > 4px).
   Direct 1080p URLs are in the chat; page links above are canonical.

STATE AT PAUSE
--------------
- Gate 2: everything measured; 0.80 unattainable at 480p (B, C, D all
  executed honestly). A' waiver recommended, NOT yet formally accepted by
  owner. Phase 3 plan approved-in-spirit but execution not started.
- §7 visual checklist (owner eyeball task) still pending — packs at
  edge/runs/phase2-review/ (90 frames) + runs/phase2-review-clip2/ (117).
- Uncommitted small edits: detection.yaml/bench.py defaults + 2 doc lines.
- Temp files (may be wiped): ffvenv, cand_*.mp4, clonetest dirs.

NEXT SESSION PICKUP LIST (in order)
-----------------------------------
1. Owner: paste downloaded clips into edge/sample_data/videos/, any names.
   Then: run verification probe (flow + density) on each — script pattern is
   in this session's chat (two probes, ~1min/clip); cut 20s @480p segments
   (ffmpeg: -ss <mid> -t 20, scale=-2:480, crf 26-28, faststart, -an);
   update SOURCES.md (Pexels License entries + author + URL); re-run gate
   metric on keepers with BOTH botsort-tuned and tracktrack-tuned.
   Candidate pages: Indian station 35333118 (verified), Delhi metro 36535410,
   Mumbai local 30381290, Mumbai interior 35741023.
2. Owner: §7 checklist verdicts (~10 min).
3. Owner: formal A' decision on Gate 2 → then Phase 3 Day 1 (zone engine)
   per Doc/Implementation/phase-3-plan.md.
4. Commit the small pending default-flip edits with the clip additions.

KEY FILES
---------
- Doc/Implementation/phase-2-completion.md §8 — all executed options + verdicts.
- edge/configs/tracktrack-tuned.yaml (new default) / botsort-reid.yaml (rejected evidence).
- edge/configs/detection.yaml — default tracker now tracktrack-tuned.
- Doc/Implementation/phase-3-plan.md — ready, awaiting A' + go.
- edge/sample_data/videos/SOURCES.md — add new clip entries there.

SESSION LESSONS
---------------
- Verify stock footage BEFORE bundling: metadata lies ("crowded" = 2.5/frame;
  "bus interior" = 0 people). Probes are cheap; redownloads are not.
- Ultralytics-native trackers differ wildly per scene; always A/B on ALL
  bundled clips, not the flagship one (tracktrack loses clip01 but wins avg).
- Config sensitivity is per-clip: a winner combo on one clip can regress
  others (buffer120/match0.8/conf0.25 helped clip01, hurt 02/03) — ship the
  best average config, document per-clip numbers in the yaml header.
