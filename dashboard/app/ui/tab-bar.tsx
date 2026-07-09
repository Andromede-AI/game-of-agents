import type { TabId } from "./types";

const TABS: { id: TabId; label: string }[] = [
  { id: "tournament", label: "Tournament" },
  { id: "marketplace", label: "Marketplace" },
  { id: "agents", label: "Agents" },
  { id: "comments", label: "Commentary" },
  { id: "events", label: "Events" },
  { id: "compare", label: "Compare" },
];

export function TabBar({
  active,
  onChange,
}: {
  active: TabId;
  onChange: (tab: TabId) => void;
}) {
  return (
    <nav className="tab-bar">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className="tab-btn"
          data-active={String(tab.id === active)}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
