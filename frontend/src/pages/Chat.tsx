import PageHeader from "../components/shared/PageHeader";
import ChatPanel from "../components/chat/ChatPanel";

export default function ChatPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Chat"
        subtitle="Send a task. Mahoraga routes it to the best agent it knows about."
      />
      <ChatPanel />
    </div>
  );
}
