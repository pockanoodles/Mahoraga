import { NavLink } from "react-router-dom";
import {
  Activity as ActivityIcon,
  Binoculars,
  Bot,
  Gauge,
  MessageSquare,
  Github,
  Moon,
  Network,
  Sun,
  Type,
} from "lucide-react";
import type { ComponentType } from "react";
import { useTheme } from "../../theme/useTheme";
import { useFont, FONT_SCHEMES } from "../../theme/useFont";
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
    label: "Command",
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

// Action row — styled like nav rows but triggers a callback instead of routing.
function ActionRow({
  Icon,
  label,
  hint,
  onClick,
  titleAttr,
}: {
  Icon: IconType;
  label: string;
  hint?: string;
  onClick: () => void;
  titleAttr?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={titleAttr}
      className="mx-2 my-0.5 flex w-[calc(100%-1rem)] items-center gap-2 rounded-md px-4 py-1.5 text-left text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground"
    >
      <Icon size={15} className="shrink-0 opacity-80" />
      <span className="flex-1 truncate">{label}</span>
      {hint && (
        <span className="font-mono text-[10px] text-muted-foreground">{hint}</span>
      )}
    </button>
  );
}

export default function Sidebar() {
  const { theme, toggle } = useTheme();
  const { current: currentFont, cycle: cycleFont } = useFont();
  const isDark = theme === "dark";

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

        {/* Settings — theme + font are click-to-cycle action rows. */}
        <div className="pb-2">
          <div className="px-6 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Settings
          </div>
          <ActionRow
            Icon={isDark ? Sun : Moon}
            label={isDark ? "Light theme" : "Dark theme"}
            hint={isDark ? "dark" : "light"}
            onClick={toggle}
            titleAttr={`Switch to ${isDark ? "light" : "dark"} theme`}
          />
          <ActionRow
            Icon={Type}
            label="Font"
            hint={
              FONT_SCHEMES.find((s) => s.id === currentFont.id)?.sampleFamily?.split(
                " "
              )[0] ?? ""
            }
            onClick={cycleFont}
            titleAttr={`Current: ${currentFont.label} — click to cycle`}
          />
        </div>
      </div>

      <div className="border-t border-sidebar-border p-3">
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
