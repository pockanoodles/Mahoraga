import { Type } from "lucide-react";
import { useFont } from "../../theme/useFont";

export default function FontPicker() {
  const { cycle, current } = useFont();
  return (
    <button
      type="button"
      onClick={cycle}
      aria-label={`Font: ${current.label} — click to cycle`}
      title="Click to try another font"
      className="flex w-full items-center gap-2 rounded-md border border-border bg-transparent px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
    >
      <Type size={14} />
      <span className="truncate" style={{ fontFamily: `"${current.sampleFamily}", system-ui, sans-serif` }}>
        {current.label}
      </span>
    </button>
  );
}
