import { NavLink } from "react-router-dom";
import {
  Activity as ActivityIcon,
  Binoculars,
  Bot,
  Gauge,
  MessageSquare,
  Github,
  Network,
} from "lucide-react";
import type { ComponentType } from "react";
import ThemeToggle from "./ThemeToggle";
import FontPicker from "./FontPicker";
import { cn } from "../../lib/cn";

type IconType = ComponentType<{ size?: number | string; className?: string }>;

interface NavItem {
  to: string;
  label: string;
  Icon: IconType;
}
interface NavSection {
  label: string;
  items: NavItem[];
}

const SECTIONS: NavSection[] = [
  {
    label: "Monitoring",
    items: [
      { to: "/performance", label: "Performance", Icon: Gauge },
      { to: "/observatory", label: "Observatory", Icon: Binoculars },
      { to: "/activity", label: "Activity", Icon: ActivityIcon },
    ],
  },
  {
    label: "Interact",
    items: [{ to: "/chat", label: "Chat", Icon: MessageSquare }],
  },
  {
    label: "Manage",
    items: [
      { to: "/agents", label: "Agents", Icon: Bot },
      { to: "/routing", label: "Routing", Icon: Network },
    ],
  },
];

export default function Sidebar() {
  return (
    <nav
      aria-label="Primary"
      className="fixed left-0 top-[var(--header-height)] z-10 flex h-[calc(100vh-var(--header-height))] w-[var(--sidebar-width)] flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground"
    >
      <div className="flex-1 overflow-y-auto py-4">
        {SECTIONS.map((section) => (
          <div key={section.label} className="pb-2">
            <div className="px-6 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {section.label}
            </div>
            {section.items.map(({ to, label, Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    "mx-2 my-0.5 flex items-center gap-2 rounded-md px-4 py-1.5 text-sm transition-colors",
                    isActive
                      ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                      : "text-sidebar-foreground hover:bg-sidebar-accent/70"
                  )
                }
              >
                <Icon size={15} className="shrink-0 opacity-80" />
                <span>{label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-2 border-t border-sidebar-border p-3">
        <ThemeToggle />
        <FontPicker />
        <a
          href="https://github.com/pockanoodles/Mahoraga"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 rounded-md px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <Github size={13} />
          <span>GitHub</span>
        </a>
      </div>
    </nav>
  );
}
