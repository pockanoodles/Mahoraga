import { useLocation } from "react-router-dom";

// Resolve a friendly page title + breadcrumb from the current pathname. Keeps
// routing concerns local so pages don't have to set their own document state.
const TITLES: Record<string, string> = {
  "/performance": "Performance",
  "/observatory": "Observatory",
  "/activity": "Activity",
  "/chat": "Chat",
  "/agents": "Agents",
  "/routing": "Routing",
};

function titleFor(path: string): string {
  return TITLES[path] ?? "Mahoraga";
}

export default function Header() {
  const { pathname } = useLocation();
  const title = titleFor(pathname);

  return (
    <header className="fixed inset-x-0 top-0 z-20 flex h-[var(--header-height)] items-center border-b border-border bg-background/90 px-5 backdrop-blur">
      <div className="flex items-center gap-2">
        <span className="font-heading text-[15px] font-semibold tracking-tight">
          Mahoraga
        </span>
        <span className="text-muted-foreground">/</span>
        <span className="text-[14px] font-medium text-foreground">{title}</span>
      </div>
      <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
        <span className="hidden font-mono sm:inline">local</span>
      </div>
    </header>
  );
}
