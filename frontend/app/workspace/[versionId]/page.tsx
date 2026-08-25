import { Workspace } from "@/components/Workspace";

export default function WorkspacePage({ params }: { params: { versionId: string } }) {
  return <Workspace versionId={params.versionId} />;
}
