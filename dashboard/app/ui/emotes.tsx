import type { ReactNode } from "react";

/**
 * Twitch-style text emotes. Rendered as styled inline spans.
 * Commentators (both LLM and mock) can use :EmoteName: syntax.
 */
const EMOTES: Record<string, { emoji: string; color: string }> = {
  PogChamp: { emoji: "😮", color: "#ff6b6b" },
  Kappa: { emoji: "😏", color: "#848494" },
  LUL: { emoji: "😂", color: "#ffca28" },
  KEKW: { emoji: "🤣", color: "#fb923c" },
  monkaS: { emoji: "😰", color: "#f87171" },
  Sadge: { emoji: "😢", color: "#38bdf8" },
  Copium: { emoji: "🤡", color: "#a970ff" },
  EZ: { emoji: "😎", color: "#00f593" },
  GG: { emoji: "🤝", color: "#34d399" },
  PepeHands: { emoji: "😭", color: "#38bdf8" },
  FiveHead: { emoji: "🧠", color: "#bf94ff" },
  pepeLaugh: { emoji: "😆", color: "#facc15" },
  OMEGALUL: { emoji: "💀", color: "#f472b6" },
  catJAM: { emoji: "🎵", color: "#00f593" },
  Stonks: { emoji: "📈", color: "#00f593" },
  NotStonks: { emoji: "📉", color: "#f44336" },
  BASED: { emoji: "👑", color: "#ffca28" },
  Clap: { emoji: "👏", color: "#bf94ff" },
  monkaW: { emoji: "😱", color: "#f87171" },
  peepoHappy: { emoji: "😊", color: "#34d399" },
  COPIUM: { emoji: "🤡", color: "#a970ff" },
  Jebaited: { emoji: "🎣", color: "#fb923c" },
  TriHard: { emoji: "💪", color: "#ffca28" },
};

const EMOTE_REGEX = /:([A-Za-z0-9]+):/g;

export function renderWithEmotes(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  // Reset regex state
  EMOTE_REGEX.lastIndex = 0;

  while ((match = EMOTE_REGEX.exec(text)) !== null) {
    const emoteName = match[1];
    const emote = EMOTES[emoteName];
    if (!emote) continue;

    // Add text before emote
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    // Add emote span
    parts.push(
      <span
        key={`${match.index}-${emoteName}`}
        className="emote"
        title={emoteName}
        style={{ color: emote.color }}
      >
        {emote.emoji}
      </span>,
    );

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}

/** List of available emote names for prompts */
export const EMOTE_NAMES = Object.keys(EMOTES);
