import { Suspense } from "react";

import { DocumentReview } from "@/components/DocumentReview";
import { Shell } from "@/components/Shell";

async function Loaded({ params }: PageProps<"/documents/[id]">) {
  const { id } = await params;
  return <DocumentReview id={id} />;
}

export default function DocumentPage(props: PageProps<"/documents/[id]">) {
  return (
    <Shell>
      <Suspense>
        <Loaded {...props} />
      </Suspense>
    </Shell>
  );
}
