# Getting Started — Agents in the Wild Workshop Experiments

## Setup (one-time)

```bash
git clone https://github.com/Andromede-AI/game-of-agents.git
cd game-of-agents
uv sync --dev
npm install
```

### Convex

```bash
npx convex login
npx convex dev --once   # pick your Convex team → create a new project → cloud
cp .env.local .env      # settings reads .env, not .env.local
```

Add API keys to `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
```

Verify: `uv run python -m analysis.cli status`

### Modal

```bash
modal profile list   # should show your Modal workspace
```

Modal is only needed for distributed runs; local runs (`goa serve` + `goa run`) work without it.

## Running Experiments

Everything goes through one CLI: `uv run python -m analysis.cli <command>`

### 1. Start the API server

```bash
uv run goa serve
```

Keep this running in a separate terminal.

### 2. Launch an experiment

```bash
# Launch and wait for completion + auto-analyze:
uv run python -m analysis.cli run configs/exp_full_homo_claude.yaml

# Launch without waiting (fire and forget):
uv run python -m analysis.cli run configs/exp_full_homo_claude.yaml --no-wait
```

### 3. Monitor a running experiment

```bash
uv run python -m analysis.cli monitor <run_id>
```

### 4. Check status of all runs

```bash
uv run python -m analysis.cli status
```

### 5. Export completed run data

```bash
uv run python -m analysis.cli export <run_id>
```

### 6. Analyze exported data

```bash
uv run python -m analysis.cli analyze <run_id>
```

### 7. Compare conditions

```bash
uv run python -m analysis.cli compare \
  baseline=run_k171het0ovc8cg \
  full_env=run_vskkr3r3ov8jeq
```

## Local Testing (no Convex/Modal)

```bash
uv run goa run configs/exp_mock_local.yaml
uv run python -m analysis.cli analyze <run_id from output>
```

## Experiment Configs

Paper-grade configs live in `configs/`; infra debug configs are in `configs/_debug/`.

| Config | Condition | Agents | Duration |
|--------|-----------|--------|----------|
| `exp_mock_local.yaml` | Local test (no infra) | 4 mock | 2 min |
| `exp_pilot_haiku.yaml` | Cheap pilot | 4 Haiku | 15 min |
| `exp_baseline_tournament_claude.yaml` | A1: pure tournament (all Claude) | 8 Sonnet | 3h |
| `exp_full_homo_claude.yaml` | B1: full env, all Claude | 8 Sonnet | 3h |
| `exp_full_heterogeneous_claude_gpt.yaml` | Heterogeneous (Claude + GPT) | 4C+4G | 3h |
| `exp_heterogeneous_baseline.yaml` | Hetero baseline (no marketplace) | 4C+4G | 3h |
| `exp_full_homo_gpt.yaml` | B3: full env, all GPT | 8 GPT | 3h |
| `exp_competitive_framing.yaml` | EXP-2: competitive framing | 8 Sonnet | 3h |
| `exp_adversarial_framing.yaml` | EXP-3: adversarial framing | 8 Sonnet | 3h |
| `exp_bad_actor.yaml` | EXP-4: 1 bad actor + 7 cooperative | 8 Sonnet | 3h |
| `exp_no_reviews.yaml` | EXP-5: marketplace without reviews | 8 Sonnet | 3h |
| `exp_additive_settlement.yaml` | EXP-6: additive payoff (confounded) | 8 Sonnet | 3h |
| `exp_additive_clean.yaml` | Clean additive (no confounded prompt) | 8 Sonnet | 3h |
| `exp_additive_clean_6h.yaml` | Clean additive, long-horizon | 8 Sonnet | 6h |
| `exp_no_reviews_additive.yaml` | No reviews × additive (stacked) | 8 Sonnet | 3h |
| `exp_hetero_additive_clean.yaml` | Hetero × additive (composition × incentive) | 4C+4G | 3h |
| `exp_full_homo_opus47.yaml` | Homo Opus 4.7 (Andon replication probe) | 8 Opus 4.7 | 3h |
| `exp_hetero_opus47_sonnet46.yaml` | Hetero Opus 4.7 / Sonnet 4.6 | 4O+4S | 3h |
| `exp_hetero_anonymized_ids.yaml` | Hetero with anonymized agent IDs (control) | 4C+4G | 3h |
| `exp_suspicious_buyers.yaml` | Buyers prompted to detect deception | 8 Sonnet | 3h |

## Common Issues

| Problem | Fix |
|---------|-----|
| `No CONVEX_DEPLOYMENT set` | Run `npx convex dev --once` |
| Settings not loading | `.env` must exist (not just `.env.local`) |
| `drain unsupported in distributed mode` | Use `goa serve` + CLI, not `goa run` |
| `Invalid API key` in agent transcripts | API key in `.env` is expired — replace it |
| `Server Error` from Convex | Functions not deployed — run `npx convex dev --once` |
| Server crashes after launching experiment | Normal — sandbox spawning is heavy. Restart with `uv run goa serve` |

## Code Structure

```
analysis/
├── cli.py          # Single entry point — all commands
├── loader.py       # Deserialize RunState JSON
├── metrics.py      # Per-agent stats, pairwise, coordination signal, Gini
├── plots.py        # Rating trajectories, aggression heatmaps
├── compare.py      # Cross-condition comparison
├── export.py       # Convex → local JSON export
├── monitor.py      # Live run monitoring
└── traces.py       # Behavioral taxonomy + LLM-as-judge prompts
```
