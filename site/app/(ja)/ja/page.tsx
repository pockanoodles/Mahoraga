import { Research } from "@/components/Research";
import { copy } from "@/content/copy";
import { metadataFor } from "../../_lib/meta";

export const metadata = metadataFor("ja", "research");

export default function Page() {
  return <Research t={copy.ja} locale="ja" />;
}
