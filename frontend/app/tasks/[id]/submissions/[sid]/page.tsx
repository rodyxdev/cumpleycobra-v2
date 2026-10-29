import { ReportScreen } from "./ReportScreen";

export default async function ReportPage({ params }: PageProps<"/tasks/[id]/submissions/[sid]">) {
  const { id, sid } = await params;
  return <ReportScreen taskId={id} submissionId={sid} />;
}
