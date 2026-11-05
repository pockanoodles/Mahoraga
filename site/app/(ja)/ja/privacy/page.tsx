import { Privacy } from "@/components/Privacy";
import { copy } from "@/content/copy";
import { metadataFor } from "../../../_lib/meta";

export const metadata = metadataFor("ja", "privacy");

export default function Page() {
  return <Privacy t={copy.ja} locale="ja" />;
}
