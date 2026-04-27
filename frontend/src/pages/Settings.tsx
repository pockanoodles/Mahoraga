import { Check, Moon, Sun } from "lucide-react";
import PageHeader from "../components/shared/PageHeader";
import Panel from "../components/shared/Panel";
import { cn } from "../lib/cn";
import { useTheme, type Theme } from "../theme/useTheme";
import { FONT_SCHEMES, useFont, type FontScheme } from "../theme/useFont";

// A row that labels a setting on the left and renders the control on the right.
// Matches the Monkeytype settings layout — label/description stacked on the
// left, interactive chips on the right.
function Row({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-3 border-b border-border px-5 py-5 last:border-b-0 md:grid-cols-[220px_1fr] md:items-start md:gap-6">
      <div>
        <div className="text-sm font-semibold text-foreground">{label}</div>
        {description && (
          <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {description}
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap gap-2">{children}</div>
    </div>
  );
}

// A chip-style selector. Active chip gets foreground + subtle ring, like
// Monkeytype's preset buttons.
function Chip({
  active,
  onClick,
  children,
  style,
  title,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  style?: React.CSSProperties;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={cn(
        "inline-flex min-w-[120px] items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "border-foreground/80 bg-accent text-accent-foreground"
          : "border-border bg-muted/40 text-muted-foreground hover:border-foreground/30 hover:bg-muted hover:text-foreground"
      )}
      style={style}
    >
      {children}
    </button>
  );
}

function ThemeChip({
  variant,
  current,
  onSelect,
}: {
  variant: Theme;
  current: Theme;
  onSelect: (t: Theme) => void;
}) {
  const active = variant === current;
  const isLight = variant === "light";
  return (
    <Chip active={active} onClick={() => onSelect(variant)}>
      <span
        aria-hidden
        className={cn(
          "inline-block h-3.5 w-3.5 rounded-full border",
          isLight
            ? "border-neutral-300 bg-[hsl(45_49%_97%)]"
            : "border-neutral-700 bg-[hsl(0_0%_9%)]"
        )}
      />
      {isLight ? (
        <Sun size={14} className="opacity-80" />
      ) : (
        <Moon size={14} className="opacity-80" />
      )}
      <span className="capitalize">{variant}</span>
      {active && <Check size={13} className="ml-auto opacity-70" />}
    </Chip>
  );
}

function FontChip({
  scheme,
  current,
  onSelect,
}: {
  scheme: (typeof FONT_SCHEMES)[number];
  current: FontScheme;
  onSelect: (s: FontScheme) => void;
}) {
  const active = scheme.id === current;
  return (
    <Chip
      active={active}
      onClick={() => onSelect(scheme.id)}
      style={{ fontFamily: `${scheme.sampleFamily}, system-ui, sans-serif` }}
      title={scheme.label}
    >
      <span className="truncate">{scheme.label}</span>
      {active && <Check size={13} className="opacity-70" />}
    </Chip>
  );
}

export default function SettingsPage() {
  const { theme, set: setTheme } = useTheme();
  const { scheme, set: setScheme } = useFont();

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <PageHeader
        title="Settings"
        subtitle="Tune the look and feel. Changes apply immediately and persist to this browser."
      />

      <div className="flex flex-col gap-5">
        <Panel title="Appearance" padded={false}>
          <Row
            label="Theme"
            description="Light or dark. Defaults to your system preference the first time."
          >
            <ThemeChip variant="light" current={theme} onSelect={setTheme} />
            <ThemeChip variant="dark" current={theme} onSelect={setTheme} />
          </Row>
        </Panel>

        <Panel title="Typography" padded={false}>
          <Row
            label="Font"
            description="Applies to the whole interface. Each chip previews its own family."
          >
            {FONT_SCHEMES.map((s) => (
              <FontChip
                key={s.id}
                scheme={s}
                current={scheme}
                onSelect={setScheme}
              />
            ))}
          </Row>
        </Panel>
      </div>
    </div>
  );
}
