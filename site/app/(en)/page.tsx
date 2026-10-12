import { Landing } from "@/components/Landing";
import { copy } from "@/content/copy";

export default function Page() {
  return <Landing t={copy.en} locale="en" />;
}
