import { Button } from "@czi-sds/components";

export function ChatBar({
  value,
  suggestions,
  drawerOpen,
  onChange,
  onSubmit,
}: {
  value: string;
  suggestions: string[];
  drawerOpen: boolean;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
}) {
  return (
    // The gradient wrapper must not eat clicks aimed at the cards behind it.
    <div className={`chat-dock${drawerOpen ? " has-drawer" : ""}`}>
      <div className="chat-card">
        {suggestions.length > 0 && (
          <div className="chat-suggestions">
            {suggestions.map((suggestion) => (
              <button
                type="button"
                key={suggestion}
                className="chat-pill"
                onClick={() => {
                  onChange(suggestion);
                  onSubmit(suggestion);
                }}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}
        <form
          className="chat-input-row"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(value);
          }}
        >
          <span className="chat-dot" />
          <input
            className="chat-input"
            value={value}
            placeholder="Refine — e.g. Xenium colon, transcripts/cell > 200"
            onChange={(event) => onChange(event.target.value)}
            aria-label="Refine the corpus query"
          />
          <Button sdsStyle="solid" sdsType="primary" type="submit">
            Search
          </Button>
        </form>
      </div>
    </div>
  );
}
