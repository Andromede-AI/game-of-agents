import type { HighlightCategory } from "./content-insights";
import type { CommentContentFilter, TabId } from "./types";

export type DashboardUrlState = {
  activeTab: TabId;
  selectedRunId: string | null;
  selectedCompareIds: string[];
  commentAuthorFilter: string;
  commentContentFilter: CommentContentFilter;
  logAgentId: string | null;
  highlightFilter: "all" | HighlightCategory;
};

function parseCsvList(value: string | null) {
  if (!value) {
    return [];
  }
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function parseDashboardUrlState(search: string): Partial<DashboardUrlState> {
  const params = new URLSearchParams(search);
  const tab = params.get("tab");
  const commentKind = params.get("commentKind");
  const highlight = params.get("highlight");
  return {
    activeTab:
      tab === "tournament" ||
      tab === "marketplace" ||
      tab === "agents" ||
      tab === "comments" ||
      tab === "events" ||
      tab === "compare"
        ? tab
        : undefined,
    selectedRunId: params.get("run"),
    selectedCompareIds: parseCsvList(params.get("compare")),
    commentAuthorFilter: params.get("commentAgent") ?? undefined,
    commentContentFilter:
      commentKind === "strategic" || commentKind === "social" || commentKind === "all"
        ? commentKind
        : undefined,
    logAgentId: params.get("logAgent"),
    highlightFilter:
      highlight === "all" ||
      highlight === "marketplace" ||
      highlight === "strategy" ||
      highlight === "competition" ||
      highlight === "chat"
        ? (highlight as "all" | HighlightCategory)
        : undefined,
  };
}

export function buildDashboardUrlSearch(state: DashboardUrlState) {
  const params = new URLSearchParams();
  params.set("tab", state.activeTab);
  if (state.selectedRunId) {
    params.set("run", state.selectedRunId);
  }
  if (state.selectedCompareIds.length > 0) {
    params.set("compare", state.selectedCompareIds.join(","));
  }
  if (state.commentAuthorFilter !== "all") {
    params.set("commentAgent", state.commentAuthorFilter);
  }
  if (state.commentContentFilter !== "all") {
    params.set("commentKind", state.commentContentFilter);
  }
  if (state.logAgentId) {
    params.set("logAgent", state.logAgentId);
  }
  if (state.highlightFilter !== "all") {
    params.set("highlight", state.highlightFilter);
  }
  return params.toString();
}
