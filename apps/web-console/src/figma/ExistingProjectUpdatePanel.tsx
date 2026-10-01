import type { UiToMainMessage } from "../../../figma-plugin/src/contracts";
import { LegacyProjectWorkflowPage, type ProjectWorkflowClientLike } from "./ProjectWorkflowPage";

export function ExistingProjectUpdatePanel({ client, postToFigma, onBack }: { client: ProjectWorkflowClientLike; postToFigma: (message: UiToMainMessage) => void; onBack(): void }) {
  return <div className="existing-update-shell"><button className="secondary-button existing-update-back" type="button" onClick={onBack}>返回新建工程</button><LegacyProjectWorkflowPage client={client} postToFigma={postToFigma} initialMode="update" updateOnly /></div>;
}
