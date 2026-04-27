import PulseCanvas from "../components/pulse/PulseCanvas";

// No PageHeader, no padding — full bleed so the visualization dominates.
export default function PulsePage() {
  return (
    <div className="h-full w-full">
      <PulseCanvas />
    </div>
  );
}
