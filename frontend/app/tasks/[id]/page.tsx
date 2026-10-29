import { TaskScreen } from "./TaskScreen";

export default async function TaskPage({ params }: PageProps<"/tasks/[id]">) {
  const { id } = await params;
  return <TaskScreen id={id} />;
}
