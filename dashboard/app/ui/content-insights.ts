export const AGENT_COLOR_VARS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--chart-7)",
  "var(--chart-8)",
];

function hashLabel(label: string) {
  let hash = 0;
  for (let index = 0; index < label.length; index += 1) {
    hash = (hash * 31 + label.charCodeAt(index)) >>> 0;
  }
  return hash;
}

export function colorForAgent(agentId: string) {
  return AGENT_COLOR_VARS[hashLabel(agentId) % AGENT_COLOR_VARS.length];
}

const COMMENT_KEYWORDS = {
  marketplace: ["offer", "offers", "buy", "bought", "sell", "selling", "price", "marketplace", "bundle"],
  performance: ["#1", "top", "best", "rank", "rating", "elo", "winning", "losing", "beat", "beats"],
  competition: ["weak", "crush", "destroy", "dominate", "opponent", "counter", "exploit"],
  alliance: ["together", "alliance", "team up", "cooperate", "good offer", "deal", "share"],
};

export type CommentTag = keyof typeof COMMENT_KEYWORDS;

export function classifyCommentContent(text: string): { tags: CommentTag[]; strategic: boolean } {
  const normalized = text.toLowerCase();
  const tags = Object.entries(COMMENT_KEYWORDS)
    .filter(([, keywords]) => keywords.some((keyword) => normalized.includes(keyword)))
    .map(([tag]) => tag as CommentTag);
  return {
    tags,
    strategic: tags.length > 0,
  };
}

const HIGHLIGHT_KEYWORDS = {
  marketplace: ["offer", "buy", "sell", "price", "marketplace", "purchase"],
  strategy: ["strategy", "improve", "bug", "fix", "monte carlo", "equity", "exploit"],
  competition: ["rank", "rating", "opponent", "winning", "losing", "beat"],
  chat: ["chat", "message", "post", "reply", "commentator"],
} as const;

export type HighlightCategory = keyof typeof HIGHLIGHT_KEYWORDS;

export function classifyTranscriptHighlight(text: string): HighlightCategory | null {
  const normalized = text.toLowerCase();
  for (const [category, keywords] of Object.entries(HIGHLIGHT_KEYWORDS)) {
    if (keywords.some((keyword) => normalized.includes(keyword))) {
      return category as HighlightCategory;
    }
  }
  return null;
}
