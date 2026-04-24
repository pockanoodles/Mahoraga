import PageHeader from "../components/shared/PageHeader";
import ChatPanel from "../components/chat/ChatPanel";

export default function ChatPage() {
  return (
    <div className="flex h-full flex-col overflow-hidden px-8 py-8">
      <PageHeader
        title="Chat"
        subtitle="Send a task. Mahoraga routes it to the best agent it knows about."
      />
      <ChatPanel />
    </div>
  );
}
