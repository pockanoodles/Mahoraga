import { useCallback, useRef, useState } from "react";

// The backend /chat endpoint emits SSE frames of the form `data: <json>\n\n`.
// For each frame, <json> is one of:
//   - a plain string (delta text from the current worker)
//   - a "[DONE]" sentinel string
//   - a "[ERROR] ..." string
//   - a metrics dict: { type: "metrics", elapsed_s, tokens, throughput_tps }
// Anything else is ignored.

export type MetricsChunk = {
  type: "metrics";
  elapsed_s: number;
  tokens: number;
  throughput_tps: number;
};

export interface StreamHandlers {
  onDelta: (text: string) => void;
  onMetrics?: (m: MetricsChunk) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

export interface ChatStream {
  streaming: boolean;
  send: (message: string, user_id: string, handlers: StreamHandlers) => Promise<void>;
  cancel: () => void;
}

export function useSSE(): ChatStream {
  const [streaming, setStreaming] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setStreaming(false);
  }, []);

  const send = useCallback(
    async (message: string, user_id: string, handlers: StreamHandlers) => {
      cancel();
      const controller = new AbortController();
      controllerRef.current = controller;
      setStreaming(true);

      try {
        const res = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, user_id }),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          handlers.onError?.(`Error: ${res.status} ${res.statusText}`);
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6);
            if (payload === "") continue;

            let chunk: unknown;
            try {
              chunk = JSON.parse(payload);
            } catch {
              chunk = payload;
            }

            if (chunk === "[DONE]") {
              handlers.onDone?.();
              return;
            }
            if (typeof chunk === "string" && chunk.startsWith("[ERROR]")) {
              handlers.onError?.(chunk);
              return;
            }
            if (chunk && typeof chunk === "object" && (chunk as MetricsChunk).type === "metrics") {
              handlers.onMetrics?.(chunk as MetricsChunk);
              continue;
            }
            if (typeof chunk !== "string") continue;
            handlers.onDelta(chunk);
          }
        }
        handlers.onDone?.();
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        handlers.onError?.(`Network error: ${(err as Error).message}`);
      } finally {
        controllerRef.current = null;
        setStreaming(false);
      }
    },
    [cancel]
  );

  return { streaming, send, cancel };
}
