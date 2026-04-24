import { Outlet } from "react-router-dom";
import Header from "./Header";
import Sidebar from "./Sidebar";

// The main content is a flex column with min-height set to the viewport below
// the header. Most pages ignore this and stack normally; pages that want to
// claim the remaining height (e.g. Chat) just `flex-1 min-h-0` their root.
export default function Shell() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <Header />
      <Sidebar />
      <main className="ml-[var(--sidebar-width)] pt-[var(--header-height)]">
        <div className="mx-auto flex min-h-[calc(100vh-var(--header-height))] max-w-[1200px] flex-col px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
