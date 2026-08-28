# Negatives Corpus — Origins & Licenses

Fight-path FP regression corpus (Phase 5, Step 5.4): open-licensed
normal-interaction footage — the Wollongong lesson made executable
("negatives are the defense"; the action model never alerts alone, and
Gate 5 demands 0 alerts over this set).

Per-clip origin URLs, durations, bytes and SHA256s live in
`manifest.csv` (committed). Media files are **gitignored** and
re-downloadable deterministically:

```bash
pip install curl_cffi   # Pexels WAF rejects plain HTTP clients
python mlops/datasets/download_negatives.py
```

| Item | Value |
|---|---|
| Files | `neg_<category>_<pexels_id>.mp4` — 147 clips, **42.4 min** |
| Categories | hug (41 / 10.0 min), play (44 / 11.5 min), rush (24 / 9.7 min), assist (17 / 3.9 min), crowd (21 / 7.3 min) |
| Origin | Pexels (per-clip page URL in manifest `pexels_url` column) |
| License | **Pexels License** — free to use and redistribute; attribution appreciated (given via per-clip origin URLs) |
| Downloaded | 2026-08-28 via `mlops/datasets/download_negatives.py` (pinned CDN URLs + SHA256 verification, resumable) |
| Resolution | ≤640×360 (smallest landscape/portrait rendition Pexels serves; ~185 MB total) |
| Selection | Pexels search per category query; clips ≥8 s; sd-quality renditions only. Curated for close physical interaction WITHOUT violence — hugs, kids/friends playing, commuters rushing, assisted walking, dense crowds (the pair-detector's hard cases) |
| Runner | `edge/tools/fight_negatives_soak.py --onnx <movinet.onnx>` — full stack per clip, exit 1 on any alert |
| Repo policy | Same posture as bundled sample videos: derivatives redistribution allowed under Pexels License with origin preserved. Media gitignored (URL-pinned re-fetch, UR Fall pattern) so the repo stays clone-light; manifest + this file are committed |

Content caveat (honest): Pexels stock skews staged/posed and
Western-context; it exercises detector + pair + fusion FP behavior on
close interaction, but it is NOT field footage — Gate 5's 0-alerts number
over this set is a necessary, not sufficient, FP bound. Transit-field
negatives enter via Phase 10's continuous data loop.
