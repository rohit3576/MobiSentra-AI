# Benchmark Datasets — Origins & Licenses

Project rule (`Doc/implementation-plan.md` §0, runbook Phase 4 Step 4.5):
benchmark datasets are downloaded by script, NEVER committed to the repo.
Every dataset used for gates must record origin, license, download date, and
citation obligations here — same pattern as `edge/sample_data/videos/SOURCES.md`.

Datasets live under their own gitignored folders (see root `.gitignore`).

## UR Fall Detection (URFD)

| Item | Value |
|---|---|
| Files | `ur_fall/fall-01-cam0.mp4` … `fall-30-cam0.mp4`, `adl-01-cam0.mp4` … `adl-40-cam0.mp4` |
| Origin | <https://fenix.ur.edu.pl/~mkepski/ds/uf.html> (dataset page, MP4 convenience encodings linked per sequence; original distribution = PNG-sequence zips) |
| Host | University of Rzeszow (Michal Kępski, ICM). Old host `fenix.unip.rzeszow.pl` is DNS-dead since ~2024; domain moved to `fenix.ur.edu.pl` |
| Downloaded | 2026-08-27 via `download_ur_fall.py` |
| License | **CC BY-NC-SA 4.0** — non-commercial academic use; contact mkepski@ur.edu.pl for commercial use |
| Citation | B. Kwolek, M. Kepski, "Human fall detection on embedded platform using depth maps and wireless accelerometer", *Computer Methods and Programs in Biomedicine* 117(3), 2014, 489–501 |
| Repo policy | Benchmark-only: clips are gitignored, never bundled, never redistributed by this project; the AGPL-3.0 code never trains on or ships them. Gate-4 numbers in `Doc/` derive from them with this citation |

Contents: 30 fall sequences (one fall each, subject remains down; cam0 =
side/horizontal Kinect view) + 40 ADL sequences (no fall). Frame-precise fall
onset annotations are no longer published on the page (the classic
`urfall-cam0-falls.csv` 404s) — Gate-4 protocol is therefore per-clip
detection (fall caught = ≥1 `fall_detected` event in the clip), the standard
protocol for pose-rule detectors on URFD.

## Le2i Fall Detection

| Item | Value |
|---|---|
| Files | `le2i/<room>/<seq>.mp4` (Coffee room ×2, Home ×2, Lecture room, Office) |
| Origin | <https://le2i.cnrs.fr/Fall%20Detection%20Dataset?lang=fr> — download is form/registration-gated → **manual acquisition** |
| License | Academic use, citation required: E. Auvinet, F. Multon, A. Saint-Arnaud, J. Rousseau, J. Meunier, "Fall detection with multiple cameras: An occlusion-robust algorithm based on 3-D skeletons vertical distribution", *IEEE Trans. Inf. Technol. Biomed.* 15(2), 2011, 290–300 |
| Repo policy | Same as URFD: gitignored, benchmark-only. Place manually downloaded zips under `le2i/`, unzip (one folder per room: Coffee room, Home, Lecture room, Office), and extend this table with the download date |

## MoViNet4Violence (altercation model weights — Phase 5, Step 5.1a)

| Item | Value |
|---|---|
| Files | `movinet/movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl/` — TF checkpoint triplet (`checkpoint`, `movinet_a2_stream_wbm.data-00000-of-00001` 35.26 MB, `.index`) + repo README snapshot; per-file SHA256s in `movinet/manifest.csv` |
| Origin | HuggingFace <https://huggingface.co/engares/MoViNet4Violence-Detection> (whole repo 1.97 GB, ~60 variants; we fetch one variant at 35 MB) |
| Companion code repo | <https://github.com/engares/MoViNets-for-Violence-Detection-in-Live-Video-Streaming> — **NO LICENSE (all rights reserved)**. Posture: **wrapper-only** — we load the checkpoint through our own `vision/action.py`, zero code copied |
| Variant choice | Evidence: engares `model_performance_metrics.csv` (fetched 2026-08-28). **A2 default** = best A2 variant: test acc 0.8122, F1_fight 0.8184, recall_fight 0.8466. **A0 fallback** = `movinet_a0_5fps_32bs_0.001lr_0.2dr_2tl` (acc 0.7672). Both 5 fps-input models. Their README's own default-best is A3-12fps (0.85) — rejected for our edge profile per phase-5-plan.md §research (12 fps input cost, unproven off x86) |
| Training data | HF tags list `DanJoshua/RWF-2000` (+ Vioperu, SCFD). **RWF-2000 is non-commercial** (implementation-plan §0) → weights inherit that restriction: benchmark-only, never bundled, never redistributed |
| HF license metadata | None (`license: null`) — treated as all-rights-reserved; same posture as the code repo |
| Downloaded | 2026-08-28 via `download_movinet.py` |
| Format note (5.1b input) | HF ships **TF checkpoints only — no .tflite, no SavedModel** on the hub. The tflite path therefore means convert-from-checkpoint (their export Colab) or full-TF load; decided at the 5.1b runtime spike |
| Citation | A. García (engares), *MoViNets for Violence Detection in Video Streaming* (GitHub, 2024); base architecture: D. Kondratyuk et al., "MoViNets: Mobile Video Networks for Efficient Video Recognition", CVPR 2021 |

## UBI-Fights (PRIMARY Gate-5 dataset — Phase 5, Step 5.5b)

| Item | Value |
|---|---|
| Files | `ubi_fights/UBI_FIGHTS/{videos/fight/F_*.mp4 (216), videos/normal/N_*.mp4 (784), annotation/*.csv (1000, frame-level), test_videos.csv (official 67-clip test split)}` |
| Origin | Official: <https://socia-lab.di.ubi.pt/EventDetection/> → `UBI_FIGHTS.zip` (8,117,112,419 bytes). **The plan's 2026-08-27 "official page unreachable" was an EXPIRED SSL CERTIFICATE** — fetch with verification relaxed; integrity pinned by hash below |
| SHA256 (zip) | `08fb9000ee7a674769ab211961b1f544d63267157ee02ea03d729bdc52c7bb33` (zip deleted after extraction to save disk; hash re-verifiable by re-download) |
| Downloaded | 2026-08-28, aria2c 16-way segmented (server allows ranges; single connection ≈ 72 KB/s, 16 connections ≈ 4.5–10 MiB/s) |
| Layout notes | videos split into `fight/` + `normal/` subdirs; annotations are ONE LABEL PER LINE (line N = frame N); filename flags: `F/N_id_indoor(0/1)_camera(0 fixed/1 rotated/2 movable)_color(0 rgb/1 gray)`. All 640×360 @30 fps, ~80 h total |
| License | Academic research, citation required: B. Degardin, H. Proença, "Human Activity Analysis: Iterative Weak/Self-Supervised Learning Frameworks for Detecting Abnormal Events", *IJCB 2020*, 1–7 |
| Repo policy | Gitignored, benchmark-only, never bundled (UR Fall posture) |

## Hockey Fights (trigger-stage Gate-5 dataset — Phase 5, Step 5.5b)

| Item | Value |
|---|---|
| Files | `hockey/fi*_xvid.avi` (500 fights) + `hockey/no*_xvid.avi` (500 non-fights) — 1000 clips, flat layout, 720×576-sourced 50-frame (~2 s) segments |
| Origin | Academic Torrents mirror: <https://academictorrents.com/details/38d9ed996a5a75a039b84cf8a137be794e7cee89> (original visilab page dead; Kaggle mirror needs owner creds — the torrent needed neither) |
| SHA256 (zip) | `c1b9c130ac62b8ddbe0337d08d3b6ecbdf908f2cb6c8e8b19217255692d741d5` (171,330,668 bytes, aria2 piece-verified; zip kept for re-verify) |
| Downloaded | 2026-08-28 via aria2c torrent, no credentials |
| License | None stated by the original authors; paper says clips "available by request" (Nievas et al., CAIP 2011). Benchmark-only posture: gitignored, never bundled, never redistributed |
| Citation | E. Bermejo Nievas, O. Deniz Suarez, G. Bueno García, R. Sukthankar, "Violence Detection in Video Using Computer Vision Techniques", *Computer Analysis of Images and Patterns (CAIP)*, 2011, 332–339 |
| Protocol note | 2 s clips cannot host the 5 fps sustain+hold alert chain — trigger-stage metrics only (pair formation + peak action score), documented in `tools/fight_benchmark.py` |
