# Clean-additive run `run_kf2kp5qrz0mmsy` — qualitative findings

> Homo-Claude, 8 agents, 3h, `settlement_mode: additive`, B1 prompts. Discovered Apr 16 during batch prep; never previously analyzed. This run doubles the clean-additive N from 1 to 2.

**Structural numbers**

| Metric | kf2k | 57vd (other clean additive) | B1 mean (N=4) |
|---|---|---|---|
| Offers | 97 | 80 | 45 |
| Purchases | 32 | 24 | 9 |
| Unique buyers | 6 / 8 | 6 / 8 | 2–3 / 8 |
| Unique sellers | 8 / 8 | — | — |
| Reviews | 24 | — | — |
| Review rate | 75% | — | — |
| Chat messages | 814 | 526 | ~400 |
| Mean price | 2.14% equity | — | — |
| Self-purchases | 0 | 0 | — |

Purchase cadence: 22 in the first hour, 7 in the second, 3 in the third. Front-loaded — consistent with agents converging on a "good enough" bot once the deployment arms race settles.

## Finding 1: Agents do explicit ROI math on marketplace decisions

This is the headline qualitative finding. Under additive settlement, agents verbalize cost/benefit reasoning in marketplace-relevant units (rating points → payout points) and act on it. This is *not* prompt-induced — the B1 prompt contains no language about ROI, equity arithmetic, or break-even thresholds.

Representative quotes (all from `run_kf2kp5qrz0mmsy`):

> **agent-4**, 19:35:36Z — *"Lowered my rank #1 strategy to 2% — currently at top of leaderboard. Full 5-card hand evaluation, position play, pot odds. Real poker logic beats heuristics. The math is straightforward: if this improves your score by more than 2%, it pays for itself."*

> **agent-5**, 19:52:07Z — *"Marketplace equity is additive to my score — that's the play. […] At #5/8 with +0.00 delta, a 2% buy that lifts my rating 1-2 points nets me 1.46-2.46 points payout-side."*

> **agent-3**, 19:51:18Z — *"To agents not in the top 3: buying a tested strategy is worth it when settlement is additive. […] If it lifts your rating even 1 point, you profit."*

Agent-3's line is especially striking: agents explicitly reference the settlement mode as the reason the buy has positive EV. Under net settlement, every equity share is a zero-sum transfer against the buyer's tournament winnings; under additive, the seller's equity is paid from a separate additive pool. Agents compute this correctly and adjust behavior accordingly.

## Finding 2: Reviews are substantive, not ceremonial

24/32 purchases (75%) receive a post-purchase review. Mean review length is 434 characters (range 249-705). Reviews frequently:

- Point out specific algorithmic choices (e.g. *"KQo=0.65 (below 0.70 strong threshold) means it just calls, not raises"*)
- Flag bugs (*"BUG: combo = tuple(s…"*)
- Quote numeric thresholds and formulas (*"pot_raise formula (pot * frac + to_call)"*)
- Evaluate design trade-offs (*"No opponent modeling or all-in handler, but the simplicity is a strength"*)

This matters for the reviews-as-trust finding in §4.2: when the review channel is available *and* incentives align (both conditions hold under clean additive), agents use it for actual information transmission, not for puffery. The 75% engagement rate is roughly 2× higher than in net-settlement runs where review activity is sparse and thin.

## Finding 3: No coordination, no price-fixing, no collusion (1/814)

Regex sweep across all 814 chat messages: one literal "coordinat-" occurrence, which on inspection is descriptive (a bot description mentioning "coordinated aggression") rather than a coordination attempt. Zero agent pairs discussed splitting markets, fixing prices, forming cartels, or refusing to sell to specific buyers. All 32 purchases happen at independently-quoted prices in the 0.9-4.0% range.

This strengthens the conclusion that clean additive's 3.1× purchase volume is driven by **independent EV calculations**, not by group-level coordination.

## Finding 4: Agent-1 dominates supply, but doesn't monopolize

Of the 8 agents, agent-1 sold 8/32 offers (25%) — the most of any seller — while also leading the tournament leaderboard throughout the run. This is the "quality-reputation loop" in action: top-ranked agents have credible signal, earn most of the marketplace share, and compound equity. But 7 other agents also made ≥1 sale (range 1-6), so the market is not winner-take-all.

Buyers cluster differently: agent-2 (9), agent-5 (7), agent-7 (6) together account for 22/32 purchases (69%). These are mid-rated agents using the marketplace to close gaps — consistent with agent-5's quoted reasoning above.

## Integration targets

- §4.3 Clean-additive discussion — pull Finding 1 quote from agent-4 or agent-3 (whichever reads cleanest at final length)
- §4.2 Reviews-as-trust — update review substance paragraph citing kf2k's 75% rate and one technical review exemplar
- Appendix / Supplementary — can include two or three longer review exemplars verbatim

## Non-findings (worth knowing)

- No *predatory* framing against other agents in chat — the regex hits on "exploit" / "predatory" are all self-references to bot techniques (e.g. "exploit fold patterns")
- No abandonment mid-run — all 8 agents continue deploying and negotiating across the full 180 min
- No "pump-and-dump" on prices — mean price 2.14% is stable across the run; agent-4 at one point *lowers* from 2% → 1.5% as quality improves, i.e. price compresses rather than inflates
