import { useEffect, useRef } from "react";
import { renderMarkdown, wireCopyButtons } from "../../lib/markdown";
import { cn } from "../../lib/cn";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
}

interface MessageBubbleProps {
  message: Message;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const isAssistant = message.role === "assistant";

  useEffect(() => {
    if (!ref.current || !isAssistant || message.streaming) return;
    wireCopyButtons(ref.current);
  }, [isAssistant, message.streaming, message.text]);

  const rendered = isAssistant && !message.streaming ? renderMarkdown(message.text) : null;

  return (
    <div className={cn("mb-4 flex", isAssistant ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-2.5 text-[14px] leading-snug",
          isAssistant
            ? "bg-card border border-border text-foreground"
            : "bg-chart-1/10 border border-chart-1/30 text-foreground"
        )}
      >
        {isAssistant ? (
          message.streaming ? (
            <div>
              {message.text.length === 0 ? (
                <span className="cursor" />
              ) : (
                <>
                  <span className="whitespace-pre-wrap">{message.text}</span>
                  <span className="cursor" />
                </>
              )}
            </div>
          ) : (
            <div ref={ref} className="md" dangerouslySetInnerHTML={{ __html: rendered ?? "" }} />
          )
        ) : (
          <span className="whitespace-pre-wrap">{message.text}</span>
        )}
      </div>
    </div>
  );
}
