import { Privacy } from "@/components/Privacy";
import { copy } from "@/content/copy";
import { metadataFor } from "../../_lib/meta";

export const metadata = metadataFor("en", "privacy");

export default function Page() {
  return <Privacy t={copy.en} locale="en" />;
}
