import { Install } from "@/components/Install";
import { copy } from "@/content/copy";
import { metadataFor } from "../../_lib/meta";

export const metadata = metadataFor("en", "install");

export default function Page() {
  return <Install t={copy.en} locale="en" />;
}
