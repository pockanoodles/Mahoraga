import { useEffect, useRef } from "react";
import { Send, X } from "lucide-react";

interface ChatInputProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  streaming: boolean;
}

export default function ChatInput({
  value,
  onChange,
  onSubmit,
  onCancel,
  streaming,
}: ChatInputProps) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [value]);

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!streaming && value.trim().length > 0) onSubmit();
    }
  };

  return (
    <div className="border-t border-border bg-card/60 px-4 py-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          placeholder="Message Mahoraga…"
          aria-label="Message"
          className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-ring focus:outline-none focus:ring-2 focus:ring-ring/20"
          style={{ maxHeight: 160 }}
        />
        {streaming ? (
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-destructive/40 bg-destructive/10 px-3 text-xs font-medium text-destructive hover:bg-destructive/20"
          >
            <X size={14} />
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={onSubmit}
            disabled={value.trim().length === 0}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Send"
          >
            <Send size={14} />
            Send
          </button>
        )}
      </div>
    </div>
  );
}
