/** Names whoever is driving the viewer over the control channel, so a human can watch. */

import type { JSX } from "react";

import { UI } from "../theme";
import { useAgentStatus } from "./useAgentChannel";

export function AgentBanner(): JSX.Element | null {
  const driving = useAgentStatus((s) => s.driving);
  const actor = useAgentStatus((s) => s.actor);
  const note = useAgentStatus((s) => s.note);
  const release = useAgentStatus((s) => s.release);

  if (!driving) return null;

  return (
    <div className="toast">
      🤖 <strong style={{ color: UI.accent }}>{actor ?? "an agent"}</strong> is driving
      {note ? ` — ${note}` : ""}
      <button
        onClick={release}
        style={{
          marginLeft: "0.6rem",
          background: "transparent",
          border: `1px solid ${UI.panelEdge}`,
          borderRadius: "0.4rem",
          color: UI.text,
          cursor: "pointer",
          padding: "0.1rem 0.5rem",
        }}
      >
        take over
      </button>
    </div>
  );
}
