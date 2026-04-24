import { useCallback, useEffect, useRef, useState } from "react";
import MessageBubble, { Message } from "./MessageBubble";
import ChatInput from "./ChatInput";
import { useSSE } from "../../hooks/useSSE";

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const streamIdRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const { streaming, send, cancel } = useSSE();

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  const appendDelta = useCallback((text: string) => {
    const id = streamIdRef.current;
    if (!id) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text: m.text + text } : m))
    );
  }, []);

  const finalize = useCallback(() => {
    const id = streamIdRef.current;
    if (!id) return;
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, streaming: false } : m)));
    streamIdRef.current = null;
  }, []);

  const showError = useCallback((msg: string) => {
    const id = streamIdRef.current;
    if (!id) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text: msg, streaming: false } : m))
    );
    streamIdRef.current = null;
  }, []);

  const handleSubmit = useCallback(() => {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: Message = { id: newId(), role: "user", text };
    const assistantId = newId();
    const assistantMsg: Message = {
      id: assistantId,
      role: "assistant",
      text: "",
      streaming: true,
    };
    streamIdRef.current = assistantId;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput("");

    void send(text, "web-user", {
      onDelta: appendDelta,
      onError: showError,
      onDone: finalize,
    });
  }, [appendDelta, finalize, input, send, showError, streaming]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card shadow-[rgba(0,0,0,0.08)_0px_0px_0px_1px]">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-5 pt-5" aria-live="polite">
        {messages.length === 0 ? (
          <div className="mt-20 text-center text-sm text-muted-foreground">
            Send a message to route a task through Mahoraga.
          </div>
        ) : (
          messages.map((m) => <MessageBubble key={m.id} message={m} />)
        )}
      </div>

      <ChatInput
        value={input}
        onChange={setInput}
        onSubmit={handleSubmit}
        onCancel={cancel}
        streaming={streaming}
      />
    </div>
  );
}
