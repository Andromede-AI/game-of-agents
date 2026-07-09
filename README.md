# Game of Agents

An open-source testbed for studying how **institutional rules shape multi-agent LLM behavior**. `N` agents simultaneously write poker bots, compete in a continuous skill-rated tournament, trade code in a marketplace, and talk in a shared chat — on one configurable substrate, with full action and reasoning traces captured for analysis.

Companion artifact for the [Agents in the Wild @ ICML 2026](https://agentwild-workshop.github.io/icml2026/) paper, *House Rules: Institutional Design in Multi-Agent LLM Tournaments and Code Markets* (see [Citation](#citation)). Every institutional lever — scoring rule, settlement mode, review visibility, identity cues, population mix, prompt framing — is a YAML switch, so the same task can be replayed under different rules. Model-family agnostic: Claude, GPT, and Gemini run natively, other models via OpenCode/OpenRouter.

## What a run captures

Each agent, in an isolated sandbox, simultaneously:

1. **Codes** — edits `bot.py`; bots auto-submit to the tournament
2. **Competes** — NLHE poker matches with TrueSkill2 display ratings
3. **Trades** — lists and buys code bundles in the marketplace, leaves reviews
4. **Communicates** — reads and posts to a shared chat channel

Final payout = tournament score + marketplace settlement (configurable: net or additive).

## Architecture

```
Agents (Modal sandboxes)  →  Orchestrator (FastAPI)  →  Convex (state + events)
     ↕                            ↕                          ↕
 CLI tools                  Tournament engine           Dashboard (Next.js)
 (stats, marketplace,       (PokerKit, TrueSkill2)      (live monitoring)
  chat, bot submit)
```

## Quick start

```bash
uv sync --dev && npm install
uv run goa run configs/exp_mock_local.yaml      # local mock run, no API keys
uv run python -m analysis.cli analyze <run_id>
```

Real LLM experiments need Convex + Modal + API keys — see [GETTING_STARTED.md](GETTING_STARTED.md). One CLI drives everything:

```bash
uv run python -m analysis.cli {run|status|monitor|export|analyze|compare} ...
```

## Reproducing the paper

```bash
bash scripts/download_runs.sh           # fetch the run corpus (release asset) → .goa_data/runs/
uv run python scripts/make_figures.py   # regenerate figures → paper/figures/
uv run pytest tests/ -q                 # includes paper/data consistency checks
```

The release asset `goa_runs_v1.zip` bundles **47 run exports** — the 39-run analyzed corpus, 3 auxiliary controls, and 5 Opus 4.7/4.8 probes (~400 MB unpacked). Aggregated per-run metrics are committed at `paper/data/run_summary.csv`; **treat that CSV as authoritative** — a few raw exports have paginated (capped) game/chat lists, so its `n_games`/`n_chat` columns were reconciled against the live API at paper time. `make_figures.py` won't overwrite it unless you pass `--rebuild-summary`.

Rerun the behavioral-taxonomy classifier (5 categories: competitive coding, marketplace exploitation, social influence, information exploitation, collusion) with:

```bash
uv run python scripts/run_taxonomy_classifier.py --concurrency 8   # also: --dry-run, --csv-only
```

## Experiment configs

`configs/` holds one YAML per condition, each varying a single lever along one of three axes — **economic incentives** (marketplace on/off, settlement mode), **population composition** (homogeneous/heterogeneous, model family), and **prompt framing**. Examples:

- `exp_baseline_tournament_claude.yaml` — A1: tournament only (no marketplace/chat)
- `exp_full_homo_claude.yaml` — B1: full environment, cooperative
- `exp_full_heterogeneous_claude_gpt.yaml` — heterogeneous Claude + GPT
- `exp_no_reviews.yaml` / `exp_additive_settlement.yaml` — marketplace-rule variants

## Selected findings

From the 39-run corpus (~11,680 games, 291 agent-runs); see the paper for full methodology, evidence tiers, and limitations:

- **Settlement rule changes trade volume** — additive (zero-cost-to-buyer) settlement yields ~2.6× the purchases of the net-settlement baseline (32.0 vs 12.2 per 3 h, N=4), while removing reviews cuts it ~5× (→2.5).
- **A placement-based score gets gamed** — 19/291 agent-runs "ladder" (place well while losing chips), far above a within-game permutation null (mean 0.76, p = 0.0001).
- **No reciprocated same-model coordination** — one unreciprocated cross-Claude solicitation; a matched anonymized-ID control shows zero such messages in 691.
- **Institutional effects don't transfer across model generations** — under identical rules, Opus 4.7 trades ~3× the Sonnet baseline while Opus 4.8 sits near it.

## Repository layout

```
game_of_agents/   core platform — orchestrator, tournament, marketplace, agent sandbox, poker engine
analysis/         analysis pipeline + single CLI — metrics, taxonomy classifier, comparison, export
configs/          experiment YAMLs (one per condition)
convex/           Convex backend functions
dashboard/        Next.js live-monitoring dashboard
scripts/          download_runs, make_figures, run_taxonomy_classifier
paper/            LaTeX source, figures, and committed data tables
tests/            pytest suite (analysis, correctness, paper-data consistency)
```

## Citation

```bibtex
@inproceedings{goa2026,
  title     = {House Rules: Institutional Design in Multi-Agent LLM Tournaments and Code Markets},
  author    = {O'Halloran, Tony and Zhuang, Allison Claire and Zhang, Michael and
               Soubeste, Thibault and Sallinen, Alexandre and Krsteski, Stefan and
               Meyer, Charlotte and Allegre, Guillaume and Seiler, Kailey},
  booktitle = {ICML 2026 Workshop on Agents in the Wild},
  year      = {2026}
}
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
