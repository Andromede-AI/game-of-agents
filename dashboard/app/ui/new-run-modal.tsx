"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "convex/react";
import { makeFunctionReference } from "convex/server";

const listProfilesRef = makeFunctionReference<"query">("profiles:list");
const saveProfileRef = makeFunctionReference<"mutation">("profiles:save");
const removeProfileRef = makeFunctionReference<"mutation">("profiles:remove");

type AgentProfile = {
  id: string;
  name: string;
  model: string;
  internetAccess: boolean;
  personality: string;
};

type AgentForm = {
  agent_id: string;
  model: string;
  internet_access: boolean;
  prompt: string;
};

type DefaultRunConfig = {
  name?: string;
  description?: string;
  duration_minutes?: number;
  last_warning_minutes?: number;
  elo_spread?: number;
  settlement_mode?: string;
  max_active_bots_per_agent?: number;
  concurrent_matches?: number;
  initial_prompt_template?: string;
  continue_prompt_template?: string;
  warning_prompt_template?: string;
  workspace_readme_template?: string;
  workspace_rules_template?: string;
  pokerkit_guide?: string;
  poker_runtime_guide?: string;
  game_time_bank_seconds?: number;
  action_increment_seconds?: number;
  game?: {
    players_per_match?: number;
    max_rounds_per_match?: number;
    game_time_bank_seconds?: number;
    action_increment_seconds?: number;
  };
  rating?: {
    matchmaking_spread?: number;
  };
  comment_feed?: {
    enabled?: boolean;
    interval_minutes?: number;
    interval_seconds?: number;
  };
  agents?: AgentForm[];
};

type RunConfigSeed = DefaultRunConfig & Record<string, unknown>;

// ── Model catalog ──
const MODEL_GROUPS = [
  {
    provider: "Anthropic (Claude Code)",
    models: [
      { id: "claude-opus-4-6", label: "Claude Opus 4.6" },
      { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
      { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
    ],
  },
  {
    provider: "OpenAI (Codex)",
    models: [
      { id: "gpt-5.4", label: "GPT 5.4" },
      { id: "o3", label: "o3" },
      { id: "o4-mini", label: "o4-mini" },
    ],
  },
  {
    provider: "Google (Gemini CLI)",
    models: [
      { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
      { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
      { id: "gemini-3-pro-preview", label: "Gemini 3 Pro Preview" },
      { id: "gemini-3-flash-preview", label: "Gemini 3 Flash Preview" },
    ],
  },
  {
    provider: "xAI (OpenCode)",
    models: [
      { id: "grok-4", label: "Grok 4" },
      { id: "grok-3", label: "Grok 3" },
    ],
  },
  {
    provider: "Other (OpenCode)",
    models: [
      { id: "deepseek-r1", label: "DeepSeek R1" },
      { id: "deepseek-v3", label: "DeepSeek V3" },
      { id: "qwen-3-coder", label: "Qwen 3 Coder" },
      { id: "qwen-3-235b", label: "Qwen 3 235B" },
    ],
  },
];

function runtimeForModel(model: string): string {
  const m = model.toLowerCase();
  if (m.startsWith("claude")) return "Claude Code";
  if (m.startsWith("gpt") || m.startsWith("o1") || m.startsWith("o3") || m.startsWith("o4")) return "Codex";
  if (m.startsWith("gemini")) return "Gemini CLI";
  return "OpenCode";
}

const GREEK = ["Alpha","Beta","Gamma","Delta","Epsilon","Zeta","Eta","Theta","Iota","Kappa","Lambda","Mu","Nu","Xi","Omicron","Pi","Rho","Sigma","Tau","Upsilon","Phi","Chi","Psi","Omega"];
const CITIES = ["Tokyo","Paris","Cairo","Lima","Oslo","Kyoto","Riga","Doha","Baku","Accra","Milan","Delhi","Seoul","Rome","Pune","Lyon","Cork","Graz","Brno","Malmö","Zürich","Porto","Quito","Dakar","Hanoi"];
const PROMPT_PLACEHOLDERS = [
  "{agent_id}",
  "{run_name}",
  "{run_description}",
  "{agent_goal}",
  "{duration_minutes}",
  "{settlement_mode}",
  "{rating_spread}",
  "{minutes_left}",
  "{best_elo}",
  "{rank}",
  "{last_summary}",
];

function randomPick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomRunName(): string {
  return `${randomPick(GREEK)}-${Math.floor(Math.random() * 900 + 100)}`;
}

function randomAgentId(): string {
  return `${randomPick(CITIES).toLowerCase()}-${Math.floor(Math.random() * 90 + 10)}`;
}

function makeDefaultAgent(): AgentForm {
  return {
    agent_id: randomAgentId(),
    model: "claude-haiku-4-5-20251001",
    internet_access: false,
    prompt: "",
  };
}

export function NewRunModal({
  open,
  onClose,
  onCreated,
  initialConfig,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
  initialConfig?: RunConfigSeed | null;
}) {
  const [baseConfig, setBaseConfig] = useState<Record<string, unknown> | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(15);
  const [lastWarningMinutes, setLastWarningMinutes] = useState(5);
  const [eloSpread, setEloSpread] = useState(150);
  const [settlementMode, setSettlementMode] = useState("net");
  const [timeBankSeconds, setTimeBankSeconds] = useState(3);
  const [actionIncrement, setActionIncrement] = useState(0);
  const [playersPerMatch, setPlayersPerMatch] = useState(2);
  const [maxRoundsPerMatch, setMaxRoundsPerMatch] = useState(50);
  const [commentFeedEnabled, setCommentFeedEnabled] = useState(false);
  const [commentFeedIntervalSeconds, setCommentFeedIntervalSeconds] = useState(60);
  const [maxBots, setMaxBots] = useState(3);
  const [concurrentMatches, setConcurrentMatches] = useState(12);
  const [initialPromptTemplate, setInitialPromptTemplate] = useState("");
  const [continuePromptTemplate, setContinuePromptTemplate] = useState("");
  const [warningPromptTemplate, setWarningPromptTemplate] = useState("");
  const [workspaceReadmeTemplate, setWorkspaceReadmeTemplate] = useState("");
  const [workspaceRulesTemplate, setWorkspaceRulesTemplate] = useState("");
  const [pokerkitGuide, setPokerkitGuide] = useState("");
  const [pokerRuntimeGuide, setPokerRuntimeGuide] = useState("");
  const [agents, setAgents] = useState<AgentForm[]>(() => [makeDefaultAgent(), makeDefaultAgent()]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [savingProfile, setSavingProfile] = useState<number | null>(null);
  const [profileName, setProfileName] = useState("");

  const profiles = useQuery(listProfilesRef) as AgentProfile[] | undefined;
  const saveProfile = useMutation(saveProfileRef);
  const deleteProfile = useMutation(removeProfileRef);

  function applyConfig(config: RunConfigSeed) {
    setBaseConfig(JSON.parse(JSON.stringify(config)) as Record<string, unknown>);
    setName(config.name || randomRunName());
    setDescription(config.description ?? "");
    setDurationMinutes(config.duration_minutes ?? 15);
    setLastWarningMinutes(config.last_warning_minutes ?? 5);
    setEloSpread(config.rating?.matchmaking_spread ?? config.elo_spread ?? 150);
    setSettlementMode(config.settlement_mode ?? "net");
    setTimeBankSeconds(
      config.game?.game_time_bank_seconds ?? config.game_time_bank_seconds ?? 3,
    );
    setActionIncrement(
      config.game?.action_increment_seconds ?? config.action_increment_seconds ?? 0,
    );
    setPlayersPerMatch(config.game?.players_per_match ?? 2);
    setMaxRoundsPerMatch(config.game?.max_rounds_per_match ?? 50);
    setCommentFeedEnabled(config.comment_feed?.enabled ?? false);
    setCommentFeedIntervalSeconds(
      config.comment_feed?.interval_seconds
        ?? ((config.comment_feed?.interval_minutes ?? 1) * 60),
    );
    setMaxBots(config.max_active_bots_per_agent ?? 3);
    setConcurrentMatches(config.concurrent_matches ?? 12);
    setInitialPromptTemplate(config.initial_prompt_template ?? "");
    setContinuePromptTemplate(config.continue_prompt_template ?? "");
    setWarningPromptTemplate(config.warning_prompt_template ?? "");
    setWorkspaceReadmeTemplate(config.workspace_readme_template ?? "");
    setWorkspaceRulesTemplate(config.workspace_rules_template ?? "");
    setPokerkitGuide(config.pokerkit_guide ?? "");
    setPokerRuntimeGuide(config.poker_runtime_guide ?? "");
    setAgents(
      config.agents && config.agents.length > 0
        ? config.agents.map((agent) => ({
            agent_id: agent.agent_id,
            model: agent.model,
            internet_access: agent.internet_access,
            prompt: agent.prompt,
          }))
        : [makeDefaultAgent(), makeDefaultAgent()],
    );
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    async function loadConfig() {
      try {
        if (initialConfig) {
          if (!cancelled) {
            applyConfig(initialConfig);
          }
          return;
        }
        const res = await fetch("/api/default-run-config");
        if (!res.ok) {
          throw new Error(`Failed to load default config: ${res.status} ${await res.text()}`);
        }
        const config = (await res.json()) as RunConfigSeed;
        if (!cancelled) {
          applyConfig(config);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load default config");
        }
      }
    }

    setError(null);
    void loadConfig();
    return () => {
      cancelled = true;
    };
  }, [initialConfig, open]);

  if (!open) return null;

  function addAgent() {
    setAgents((prev) => [...prev, makeDefaultAgent()]);
  }

  function removeAgent(index: number) {
    setAgents((prev) => prev.filter((_, i) => i !== index));
  }

  function updateAgent(index: number, field: keyof AgentForm, value: string | boolean) {
    setAgents((prev) =>
      prev.map((a, i) => (i === index ? { ...a, [field]: value } : a)),
    );
  }

  function loadProfile(index: number, profile: AgentProfile) {
    setAgents((prev) =>
      prev.map((a, i) =>
        i === index
          ? { ...a, agent_id: profile.name.toLowerCase(), model: profile.model, internet_access: profile.internetAccess, prompt: profile.personality }
          : a,
      ),
    );
  }

  async function handleSaveProfile(index: number) {
    const agent = agents[index];
    const name = profileName.trim();
    if (!name) return;
    await saveProfile({
      name,
      model: agent.model,
      internetAccess: agent.internet_access,
      personality: agent.prompt,
    });
    setSavingProfile(null);
    setProfileName("");
  }

  async function handleDeleteProfile(id: string) {
    await deleteProfile({ id: id as never });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (!agents.some((a) => a.agent_id.trim())) {
      setError("At least one agent with an ID is required");
      return;
    }

    const body = {
      ...(baseConfig ?? {}),
      name: name.trim(),
      description: description.trim(),
      duration_minutes: durationMinutes,
      last_warning_minutes: lastWarningMinutes,
      settlement_mode: settlementMode,
      max_active_bots_per_agent: maxBots,
      concurrent_matches: concurrentMatches,
      initial_prompt_template: initialPromptTemplate,
      continue_prompt_template: continuePromptTemplate,
      warning_prompt_template: warningPromptTemplate,
      workspace_readme_template: workspaceReadmeTemplate,
      workspace_rules_template: workspaceRulesTemplate,
      pokerkit_guide: pokerkitGuide,
      poker_runtime_guide: pokerRuntimeGuide,
      game: {
        ...((baseConfig?.game as Record<string, unknown> | undefined) ?? {}),
        match_format: "freezeout",
        players_per_match: playersPerMatch,
        max_rounds_per_match: maxRoundsPerMatch,
        game_time_bank_seconds: timeBankSeconds,
        action_increment_seconds: actionIncrement,
      },
      rating: {
        ...((baseConfig?.rating as Record<string, unknown> | undefined) ?? {}),
        system: "trueskill2",
        matchmaking_spread: eloSpread,
        leaderboard_score: "conservative",
      },
      comment_feed: {
        ...((baseConfig?.comment_feed as Record<string, unknown> | undefined) ?? {}),
        enabled: commentFeedEnabled,
        interval_seconds: commentFeedIntervalSeconds,
      },
      agents: agents
        .filter((a) => a.agent_id.trim())
        .map((a) => ({
          agent_id: a.agent_id.trim(),
          model: a.model,
          internet_access: a.internet_access,
          prompt: a.prompt,
          // runtime and command are auto-resolved by the backend from model
        })),
    };

    setSubmitting(true);
    try {
      const createRes = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!createRes.ok) {
        throw new Error(`Create failed: ${createRes.status} ${await createRes.text()}`);
      }
      const { run_id } = await createRes.json();

      const startRes = await fetch(`/api/runs/${run_id}/start`, {
        method: "POST",
      });
      if (!startRes.ok) {
        throw new Error(`Start failed: ${startRes.status} ${await startRes.text()}`);
      }

      onCreated(run_id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="new-run-modal panel" onClick={(e) => e.stopPropagation()}>
        <div className="new-run-modal__header">
          <h2>New Run</h2>
          <button type="button" className="btn btn--sm" onClick={onClose}>Close</button>
        </div>

        <form onSubmit={handleSubmit} className="new-run-modal__form">
          {error && <div className="new-run-modal__error">{error}</div>}

          <fieldset>
            <legend>Basic Settings</legend>
            <label>
              Name
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              Description
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
            </label>
            <div className="form-row">
              <label>
                Duration (min)
                <input type="number" value={durationMinutes} onChange={(e) => setDurationMinutes(+e.target.value)} min={1} />
              </label>
              <label>
                Last Warning (min)
                <input type="number" value={lastWarningMinutes} onChange={(e) => setLastWarningMinutes(+e.target.value)} min={0} />
              </label>
            </div>
          </fieldset>

          <fieldset>
            <legend>Tournament Settings</legend>
            <div className="form-row">
              <label>
                Rating Spread
                <input type="number" value={eloSpread} onChange={(e) => setEloSpread(+e.target.value)} />
              </label>
              <label>
                Settlement
                <select value={settlementMode} onChange={(e) => setSettlementMode(e.target.value)}>
                  <option value="net">Net</option>
                  <option value="additive">Additive</option>
                </select>
                <span className="label-hint">
                  Marketplace prices are percentages of the buyer&apos;s final best-bot tournament score. In net mode the buyer gives up that percentage; in additive mode the seller gets it on top.
                </span>
              </label>
            </div>
            <div className="form-row">
              <label>
                Players / Match
                <input type="number" value={playersPerMatch} onChange={(e) => setPlayersPerMatch(+e.target.value)} min={2} max={8} />
              </label>
              <label>
                Hand Cap
                <input type="number" value={maxRoundsPerMatch} onChange={(e) => setMaxRoundsPerMatch(+e.target.value)} min={1} />
              </label>
            </div>
            <div className="form-row">
              <label>
                Time Bank (s)
                <input type="number" value={timeBankSeconds} onChange={(e) => setTimeBankSeconds(+e.target.value)} min={0} />
              </label>
              <label>
                Increment (s)
                <input type="number" value={actionIncrement} onChange={(e) => setActionIncrement(+e.target.value)} min={0} />
              </label>
            </div>
            <div className="form-row">
              <label>
                Max Bots/Agent
                <input type="number" value={maxBots} onChange={(e) => setMaxBots(+e.target.value)} min={1} />
              </label>
              <label>
                Concurrent Matches
                <input type="number" value={concurrentMatches} onChange={(e) => setConcurrentMatches(+e.target.value)} min={1} />
              </label>
            </div>
            <div className="form-row">
              <label className="checkbox-label">
                <input type="checkbox" checked={commentFeedEnabled} onChange={(e) => setCommentFeedEnabled(e.target.checked)} />
                Comment Feed
              </label>
              <label>
                Comment Interval (sec)
                <input type="number" value={commentFeedIntervalSeconds} onChange={(e) => setCommentFeedIntervalSeconds(+e.target.value)} min={1} />
              </label>
            </div>
          </fieldset>

          <fieldset>
            <legend>Prompt Templates</legend>
            <details>
              <summary>Show prompt and guide templates</summary>
              <p>
                Available placeholders: {PROMPT_PLACEHOLDERS.map((value, index) => (
                  <span key={value}>
                    <code>{value}</code>
                    {index < PROMPT_PLACEHOLDERS.length - 1 ? ", " : ""}
                  </span>
                ))}
              </p>
              <p>
                Not every placeholder is meaningful in every template. For example,
                <code>{" {minutes_left} "}</code>
                and <code>{" {last_summary} "}</code> are mainly for step prompts, while
                <code>{" {duration_minutes} "}</code> and <code>{" {rating_spread} "}</code>
                are mainly for workspace docs.
              </p>
              <p>
                These template defaults are loaded from the backend YAML config. The base prompts already explain marketplace equity and settlement; editing them here overrides those defaults for this run.
              </p>
              <label>
                Initial Prompt Template
                <textarea
                  value={initialPromptTemplate}
                  onChange={(e) => setInitialPromptTemplate(e.target.value)}
                  rows={10}
                />
              </label>
              <label>
                Continue Prompt Template
                <textarea
                  value={continuePromptTemplate}
                  onChange={(e) => setContinuePromptTemplate(e.target.value)}
                  rows={10}
                />
              </label>
              <label>
                Warning Prompt Template
                <textarea
                  value={warningPromptTemplate}
                  onChange={(e) => setWarningPromptTemplate(e.target.value)}
                  rows={8}
                />
              </label>
              <label>
                Workspace README Template
                <textarea
                  value={workspaceReadmeTemplate}
                  onChange={(e) => setWorkspaceReadmeTemplate(e.target.value)}
                  rows={12}
                />
              </label>
              <label>
                Workspace Rules Template
                <textarea
                  value={workspaceRulesTemplate}
                  onChange={(e) => setWorkspaceRulesTemplate(e.target.value)}
                  rows={8}
                />
              </label>
              <label>
                PokerKit Guide
                <textarea
                  value={pokerkitGuide}
                  onChange={(e) => setPokerkitGuide(e.target.value)}
                  rows={12}
                />
              </label>
              <label>
                Poker Runtime Guide
                <textarea
                  value={pokerRuntimeGuide}
                  onChange={(e) => setPokerRuntimeGuide(e.target.value)}
                  rows={14}
                />
              </label>
            </details>
          </fieldset>

          <fieldset className="agents-fieldset">
            <legend>Agents</legend>
            {agents.map((agent, i) => (
              <div key={i} className="agent-form-row">
                <div className="profile-bar">
                  <select
                    value=""
                    onChange={(e) => {
                      const p = profiles?.find((p) => p.id === e.target.value);
                      if (p) loadProfile(i, p);
                    }}
                  >
                    <option value="" disabled>Load profile...</option>
                    {(profiles ?? []).map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                  {savingProfile === i ? (
                    <div className="profile-save-row">
                      <input
                        type="text"
                        value={profileName}
                        onChange={(e) => setProfileName(e.target.value)}
                        placeholder="Profile name..."
                        autoFocus
                      />
                      <button type="button" className="btn btn--sm btn--accent" onClick={() => handleSaveProfile(i)}>Save</button>
                      <button type="button" className="btn btn--sm" onClick={() => { setSavingProfile(null); setProfileName(""); }}>Cancel</button>
                    </div>
                  ) : (
                    <button type="button" className="btn btn--sm" onClick={() => setSavingProfile(i)}>Save as profile</button>
                  )}
                </div>
                <div className="form-row">
                  <label>
                    Agent ID
                    <input type="text" value={agent.agent_id} onChange={(e) => updateAgent(i, "agent_id", e.target.value)} />
                  </label>
                  <label>
                    Model
                    <select value={agent.model} onChange={(e) => updateAgent(i, "model", e.target.value)}>
                      {MODEL_GROUPS.map((group) => (
                        <optgroup key={group.provider} label={group.provider}>
                          {group.models.map((m) => (
                            <option key={m.id} value={m.id}>{m.label}</option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </label>
                  <label className="checkbox-label">
                    <input type="checkbox" checked={agent.internet_access} onChange={(e) => updateAgent(i, "internet_access", e.target.checked)} />
                    Internet
                  </label>
                </div>
                <div className="model-hint">
                  CLI: {runtimeForModel(agent.model)} &middot; {agent.model}
                </div>
                <label>
                  Personality <span className="label-hint">(added to the base prompt)</span>
                  <textarea value={agent.prompt} onChange={(e) => updateAgent(i, "prompt", e.target.value)} rows={2} placeholder="Aggressive bluffer, willing to take big risks..." />
                </label>
                <div className="agent-form-row__actions">
                  {agents.length > 1 && (
                    <button type="button" className="btn btn--sm btn--danger" onClick={() => removeAgent(i)}>Remove</button>
                  )}
                </div>
              </div>
            ))}

            {profiles && profiles.length > 0 && (
              <div className="profile-list">
                <strong>Saved Profiles</strong>
                <div className="profile-chips">
                  {profiles.map((p) => (
                    <span key={p.id} className="profile-chip">
                      {p.name}
                      <button type="button" className="profile-chip__delete" onClick={() => handleDeleteProfile(p.id)} title="Delete profile">&times;</button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="agents-fieldset__add">
              <button type="button" className="btn btn--accent btn--block" onClick={addAgent}>+ Add Agent</button>
            </div>
          </fieldset>

          <button type="submit" className="btn btn--accent btn--block" disabled={submitting}>
            {submitting ? "Creating..." : "Create & Start Run"}
          </button>
        </form>
      </div>
    </div>
  );
}
