import { Engine } from "@/components/Engine";
import { copy } from "@/content/copy";
import { metadataFor } from "../../_lib/meta";

export const metadata = metadataFor("en", "engine");

export default function Page() {
  return <Engine t={copy.en} locale="en" />;
}
