import { Research } from "@/components/Research";
import { copy } from "@/content/copy";
import { metadataFor } from "../_lib/meta";

export const metadata = metadataFor("en", "research");

export default function Page() {
  return <Research t={copy.en} locale="en" />;
}
