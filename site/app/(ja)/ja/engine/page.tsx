import { Engine } from "@/components/Engine";
import { copy } from "@/content/copy";
import { metadataFor } from "../../../_lib/meta";

export const metadata = metadataFor("ja", "engine");

export default function Page() {
  return <Engine t={copy.ja} locale="ja" />;
}
