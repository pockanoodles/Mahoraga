import { Outlet } from "react-router-dom";
import Header from "./Header";
import Sidebar from "./Sidebar";

// Fixed-height viewport box. Each page owns its own scroll behavior:
//   - Normal pages (Performance, Activity, Agents, Routing, Settings)
//     wrap content in `h-full overflow-y-auto px-8 py-8`.
//   - Bounded pages (Observatory, Chat) use `flex h-full flex-col overflow-hidden`
//     so their inner regions can scroll independently without pushing the page.
export default function Shell() {
  return (
    <div className="h-screen overflow-hidden bg-background text-foreground">
      <Header />
      <Sidebar />
      <main className="ml-[var(--sidebar-width)] h-screen pt-[var(--header-height)]">
        <div className="mx-auto h-[calc(100vh-var(--header-height))] max-w-[1200px]">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
