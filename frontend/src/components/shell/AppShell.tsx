// The window: a sidebar of places, and the page. The audit comes first because
// it is the product; the router is the stage that acts on the audit's verdict.
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Cpu, FolderGit2, Gauge, History, Menu, Route, Settings, X } from "lucide-react";
import { useEngine } from "@/lib/engine";
import { Mark } from "../Mark";
import { Command } from "../ui/layout";

const NAV = [
  { group: "Audit", items: [
    { to: "/", label: "Verdict", icon: Gauge, key: "1" },
    { to: "/repositories", label: "Repositories", icon: FolderGit2, key: "2" },
    { to: "/runs", label: "Runs", icon: History, key: "3" },
    { to: "/arms", label: "Arms", icon: Cpu, key: "4" },
  ] },
  { group: "Serve", items: [{ to: "/router", label: "Router", icon: Route, key: "5" }] },
];
const ALL = [...NAV.flatMap((g) => g.items), { to: "/settings", label: "Settings", icon: Settings, key: "," }];

export function AppShell() {
  const { engine, error, reload } = useEngine();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => setOpen(false), [location.pathname]);

  // ⌘1–5 jump between places and ⌘, opens Settings, as in a Mac app.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      const hit = ALL.find((i) => i.key === e.key);
      if (hit) {
        e.preventDefault();
        navigate(hit.to);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  const offline = error?.unreachable || (error && error.status === 404);

  return (
    <div className="app" data-nav-open={open || undefined}>
      <aside className="sidebar" aria-label="Mahoraga">
        <div className="sidebar__brand">
          <Mark size={22} />
          <span className="sidebar__name">Mahoraga</span>
          {engine && <span className="sidebar__version">{engine.version}</span>}
          <button type="button" className="sidebar__close" onClick={() => setOpen(false)} aria-label="Close navigation">
            <X size={18} />
          </button>
        </div>
        <nav className="sidebar__nav">
          {NAV.map((g) => (
            <div key={g.group} className="sidebar__group">
              <p className="sidebar__heading">{g.group}</p>
              {g.items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === "/"} className="sidebar__item" aria-keyshortcuts={`Meta+${i.key}`}>
                  <i.icon size={16} strokeWidth={1.75} aria-hidden />
                  {i.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar__foot">
          <NavLink to="/settings" className="sidebar__item" aria-keyshortcuts="Meta+,">
            <Settings size={16} strokeWidth={1.75} aria-hidden />
            Settings
          </NavLink>
          <EngineStatus />
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button type="button" className="topbar__menu" onClick={() => setOpen(true)} aria-label="Open navigation">
            <Menu size={20} />
          </button>
          <span className="topbar__brand">
            <Mark size={18} />
            Mahoraga
          </span>
        </header>
        {engine?.demo && (
          <div className="demo-banner" role="note">
            <strong>Demo data.</strong> You're looking at the published 18-commit probe on Mahoraga's own history
            (September 28 and 29, 2026), so nothing here came from your machine.
          </div>
        )}
        {offline ? <Offline onRetry={reload} notFound={error?.status === 404} /> : <Outlet />}
      </div>
      <button type="button" className="scrim" aria-label="Close navigation" onClick={() => setOpen(false)} tabIndex={-1} />
    </div>
  );
}

function EngineStatus() {
  const { engine, error } = useEngine();
  if (error) {
    return (
      <p className="engine-status">
        <span className="dot dot--off" /> Engine offline
      </p>
    );
  }
  if (!engine) return <p className="engine-status engine-status--placeholder" aria-hidden />;
  return (
    <p className="engine-status" title={engine.ollama.url}>
      <span className={`dot ${engine.ollama.up ? "dot--on" : "dot--off"}`} />
      {engine.ollama.up ? `Ollama ${engine.ollama.version ?? ""}`.trim() : "Ollama not running"}
    </p>
  );
}

function Offline({ onRetry, notFound }: { onRetry: () => void; notFound: boolean }) {
  // Keep knocking while the page is open, so starting the engine is all it takes.
  useEffect(() => {
    const id = window.setInterval(onRetry, 3000);
    return () => window.clearInterval(id);
  }, [onRetry]);
  return (
    <div className="offline">
      <Mark size={40} className="offline__mark" />
      <h1 className="offline__title">{notFound ? "This engine is older than the app" : "The engine isn't running"}</h1>
      <p className="offline__body">
        {notFound
          ? "It answers, but it has no /api/engine. Update Mahoraga and restart the server."
          : "Everything here comes from the Mahoraga engine on this machine. Start it and this page connects by itself."}
      </p>
      <Command>{notFound ? "uv tool upgrade mahoraga" : "mahoraga serve"}</Command>
    </div>
  );
}
