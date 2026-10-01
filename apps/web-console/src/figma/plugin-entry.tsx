import { createRoot } from "react-dom/client";
import { ProjectWorkflowClient } from "../../../figma-plugin/src/project-client";
import { ProjectWorkflowPage } from "./ProjectWorkflowPage";

declare const __FGUI_SERVER_ORIGIN__: string;
declare const __FGUI_PLUGIN_ACCESS_TOKEN__: string;

const root = document.getElementById("root");
if (root) {
  const client = new ProjectWorkflowClient({
    serverOrigin: __FGUI_SERVER_ORIGIN__,
    pluginToken: __FGUI_PLUGIN_ACCESS_TOKEN__,
  });
  createRoot(root).render(<ProjectWorkflowPage client={client} defaultMode="writer" />);
}
