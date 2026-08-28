# Bus Reality Research — What Actually Happens in Buses

> **Web research, 2026-08-28** (6 search waves: India incidents/stats,
> MoRTH/NCRB data, harassment studies, non-collision fall literature,
> operator-assault data, LatAm/global comparisons). Purpose: validate the
> MVP event taxonomy against real-world incident patterns and source the
> post-MVP backlog. No code changed. URLs verified live on search day.

## Why this exists

MobiSentra's MVP slice (falls, overcrowding, altercation, zones) was chosen
from priors. This doc checks that choice against measured incident reality
— India first (target deployment), then global — and lists what the data
says we should queue next.

---

## 1. India findings (ranked by measured impact)

| # | Issue | Evidence |
|---|---|---|
| 1 | **Falls while boarding/alighting — footboard travel** | MoRTH 2024: **~48% of fatal bus accidents = passengers falling from footboards or while boarding/alighting**. Bengaluru fatal-crash micro-analysis (Kharola/Tiwari/Mohan, JPT 13(4), 2010): bus passengers = 18% of bus-crash fatalities; **92% of those died boarding/alighting**; floors ~1200 mm high; 36% of victims crushed under wheels (85% rear wheels). TGSRTC Hyderabad: ~2,800 City Ordinary/Metro Express buses operate **without doors** (New Indian Express, Jun 2024). Fresh incidents: Pune PMPML girl fell from front door, run over (Aug 2026); SETC student fell from footboard, Chennai–Tirupati highway (Jul 2026) |
| 2 | **Overcrowding** | Structural: Pune PMPML legal cap 33 seated + 17 standing, routinely exceeded; 11–12 lakh passengers/day on ~1,700–1,800 operating buses (fleet 2,045); peak windows 8–10:30 and 16:20:00 (TOI, Aug 2026). Consequences chain: crush → footboard travel → falls; suffocation complaints; enables harassment |
| 3 | **Harassment of women** | World Bank/UN Women: 88% Delhi, 75% Mumbai, 63% Pune, >50% Chennai women harassed in public transport; reported: 1% / 6% / 12%. Hyderabad study (Ali et al., TRR 2025, n=583): **buses = most common location for physical harassment (touching); overcrowdedness = the main stated concern**. Delhi HVT report: 69% of women feel unsafe *inside* the bus; only 5% knew about panic buttons before a targeted campaign (65% after) |
| 4 | **Rash driving + crashes** | Karnataka transport dept: 5,777 rash bus-driving cases 2023–25, **>60% state-run buses** (TOI, Feb 2026). Buses = 12–20% of fatal crashes in Indian cities (Kharola 2010); DTC study: buses in 5–10% of total crashes in India vs 0.5–1.6% in developed nations. Driver fatigue recurring (as little as 4 h sleep, no mandatory reserve driver — TOI 2019 Yamuna Expressway analysis) |
| 5 | **Fire** | Rare, catastrophic: Chitradurga sleeper-bus fire killed 8 (Dec 2025) — inflammable cargo routinely smuggled in passenger buses (Karnataka legislative council debate, Feb 2026) |
| 6 | **Property crime** | Gold-chain snatching, pickpocketing in crush crowds (Lucknow eve-teasing study, Crime Science 2017; Chennai rapid assessment 2016) |

## 2. Global findings (US / UK / EU / East Asia / LatAm)

| # | Issue | Evidence |
|---|---|---|
| 1 | **Non-collision falls (sudden braking) — the dominant injury source** | UK (Kirk & Grant, Loughborough 2003): **64.3% of all killed-or-seriously-injured bus passengers = non-collision**; 74.2% female; 58% aged 60+; >6,000 injured/yr on UK buses, half 65+. Germany (Bavaria in-depth): 50% of bus casualties non-collision, **>70% caused by emergency braking**. Korea (Jeong et al. 2022, digital-tachograph study): **abrupt deceleration and abrupt stop are the only statistically significant predictors** of passenger falls (speeding, sudden starts, turns — not significant). China (court-records study, TRID): fall injuries #1, **door-clamp injuries #2** (hurried door closing); older women most injured. Hong Kong (17,383 injured passengers, 10 yrs): elderly women most vulnerable, esp. while standing/boarding/alighting. US WMATA: 1/3 of non-collision injuries during boarding/alighting, 1/4 during stopping. Aon 2025 transit benchmark: falls = 27% of non-auto claims, entry/exit = 16% |
| 2 | **Assaults — on riders AND operators** | US (Urban Institute, NTD data): major assaults on transit workers **tripled 2008→2022** (168→492/yr, undercount); NYC MTA now ~1 major assault per 1.4 days. FTA/NAP (Apr 2023–Jun 2024): 2,959 major security events across 106 agencies; **55% occurred in/on vehicles**; buses: 1,049 assaults — 430 on operators; ~18k non-major assaults, 56.8% nonphysical on operators; fare disputes = the recurring trigger. Maryland FY2023: 83% of operator assaults on buses. London (TfL FOI, Sep 2025): 818 driver assaults/hate crimes 2024, +20% YoY, "daily occurrence" per Unite |
| 3 | **Harassment** | Bogotá TransMilenio: 37% of female users experienced unwanted sexual contact; boarding crowds "provide camouflage" (Kash 2019). Barranquilla BRT: >60% harassed; **overcrowding = the strongest risk factor** (Orozco-Fontalvo 2019). Mexico City: 65% of women (govt figures); Brazil: 44% (ActionAid). Buenos Aires: harassment on buses 5× more than metro. 18-city global study: victimization >70% in São Paulo/Lagos |

## 3. The thesis-validating finding

> "CCTV cameras are generally more effective in evidence collection **after
> a crime has occurred unless they are actively monitored in real-time**, in
> which case they serve as a proactive crime prevention tool."
> — *Safe and Secure Public Transport in Delhi* (HVT055, 2024)

Deployment reality: UK ≈ 100% of buses have CCTV (Statista 2021); MTA
Maryland runs 7 interior + 3 exterior cameras per bus, 30-day retention,
remote access; DTC Delhi already fields CCTV + panic buttons + AVL.
**Everyone records; nobody watches.** Real-time AI monitoring of existing
feeds is exactly the gap — the README's "footage nobody watches" line is
now source-backed.

Related legal note: courts repeatedly split on sudden-braking injury claims
for lack of objective evidence (Brant v. Tri-Met, Or. Ct. App. 2009;
Gioulis v. MTA Bus Co., NY App. Div. 2012 both turned on "was the stop
unusual and violent") — automated evidence clips + event timestamps have
direct evidentiary value for operators.

## 4. Mapping to MobiSentra

| Real-world issue | Coverage |
|---|---|
| Falls inside bus (braking, elderly) | ✅ Phase 4 fall detector (93.3% UR Fall) — matches the #1 global injury pattern |
| Overcrowding | ✅ Phase 3 occupancy — India's #2 issue and the *enabler* of #1 and #3 |
| Boarding/alighting + door-zone risk | ✅ Phase 3 door_roi dwell — mechanism already ships |
| Assaults/fights (riders + operators) | 🚧 Phase 5 altercation (in flight) |
| Footboard travel (India's #1 fatal pattern) | ⚠️ Gap — cheap with existing machinery: door_roi occupancy + vehicle-motion input; Phase 3 already reserved the door/vehicle-telemetry MQTT slot |
| Harassment | ⚠️ Constrained by design (responsible-use policy: no identity/gender inference). Ethical path = panic-button ingestion → event engine + auto-attached evidence clip (the "follow-up protocol" the research says is missing) |
| Medical collapse ("felt dizzy and fell" — Pune case) | ~50% covered by fall detector (collapse pattern); intent-distinction is post-MVP |
| Fire/smoke | Outside CV scope — sensor fusion, far post-MVP |
| Harsh-braking driver coaching | Gap — Korea/UK data show deceleration events predict falls; needs motion input (CAN/IMU) fused with fall events |

## 5. Post-MVP backlog additions (proposed 2026-08-28)

1. **Footboard-travel detection** — person occupying door zone while
   vehicle in motion → event. Machinery exists (door_roi + reserved
   telemetry slot). Highest India-specific value per engineering cost.
2. **Panic-button event ingestion** — MQTT input → CloudEvents envelope →
   auto-attach the 5 s evidence ring (already buffered). Cheapest ethical
   harassment response; DTC-style buttons already deployed in the field.
3. **Harsh-braking ↔ fall correlation** — deceleration events (CAN/IMU)
   correlated with fall detections → driver-coaching signal + objective
   incident evidence (the court cases above).
4. **Medical-emergency distinction** — collapse-without-recovery posture
   signature, longer horizon than the fall confirm window.
5. Fire/smoke — explicit non-goal for CV MVP; revisit with sensor fusion.

Dashboard note (Phase 9 realism): peak overcrowding windows are
8–10:30 / 16–20 — demo scenarios and synthetic-event timing should match.

## Sources

- MoRTH *Road Accidents in India 2024* (indiairf.com PDF) + New Indian Express Chennai (Jul 23, 2026) for the 48% footboard figure
- Kharola, Tiwari, Mohan — *Traffic Safety and City Public Transport System: Case Study of Bengaluru* (JPT 13(4), 2010; doi.org/10.5038/2375-0901.13.4.4)
- Gijre, Ram — *Spatial and Temporal Pattern of Bus Crashes… DTC* (Journal of Road Safety, 2023)
- Indian Express Pune (Aug 26, 2026) — PMPML 60 deaths since 2024; TOI Pune (Aug 24, 2026) — overcrowding/fleet; TOI Bengaluru (Feb 12, 2026) — Karnataka rash driving + fires; New Indian Express Telangana (Jun 18, 2024) — doorless TGSRTC fleet
- Ali, Sahu, Majumdar et al. — *Women Commuters' Safety… Hyderabad* (Transportation Research Record, 2025; doi 10.1177/03611981251339176)
- *Safe and Secure Public Transport in Delhi* — HVT055 final report (transport-links.com, 2024) — harassment %s, panic-button awareness, CCTV-effectiveness quote
- Tripathi, Borrion, Belur — *Sexual harassment of students on public transport: Lucknow* (Crime Science, 2017); Natarajan — *Rapid assessment of "eve teasing"… Chennai* (Crime Science, 2016)
- Kirk, Grant, Bird — *Passenger casualties in non-collision incidents on buses and coaches in Great Britain* (Loughborough, 2003)
- *Systematic review of… non-collision injuries… older people… public buses* (J Transport & Health, 2016; 18–33% of ED attenders = fractures/dislocations)
- Jeong, Park, Lee, Park, Yun — *Influence of Public Bus Driver's Driving Behaviors on Passenger Fall Incidents: Digital Tachograph Data* (Adv. Civ. Eng., 2022)
- *Noncollision injuries to passengers on buses: China case study* (TRID 2343385); Hong Kong 10-yr police dataset study (Accid. Anal. Prev.)
- FTA — *Bus Safety Data Report 2008–2018*; Urban Institute — *Assaults on Transit Workers Have Tripled* (Nov 2023); NAP — *Mitigation Strategies for Deterring Transit Assaults* (2024, Apr 2023–Jun 2024 window); Maryland DLS — *Assaults on Public Transit Operators §7-714* (FY2023)
- The Standard (London) — driver assaults 818 in 2024, +20% (Sep 2025); Statista — UK bus CCTV share ~100% (2021)
- Kash — Colombia/Bolivia transit sexual assault study (2019); Orozco-Fontalvo — Barranquilla BRT harassment (J Transport & Health, 2019); World Bank feature — LatAm harassment (2014); Ceccato/Loukaitou-Sideris 18-city study (VGS, 2021)
- Court records: Brant v. Tri-County Metropolitan Transit District (213 P.3d 869, Or. Ct. App. 2009); Gioulis v. MTA Bus Co. (94 A.D.3d 811, NY App. Div. 2012)
