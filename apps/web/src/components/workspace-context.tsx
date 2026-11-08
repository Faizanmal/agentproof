"use client";

import { createContext, useContext } from "react";

export const WorkspaceContext = createContext<{
  workspaces: import("@agentproof/contracts/schema").components["schemas"]["WorkspaceView"][];
  selectedWorkspaceId: string;
  setSelectedWorkspaceId: (value: string) => void;
}>({ workspaces: [], selectedWorkspaceId: "", setSelectedWorkspaceId: () => undefined });

export function useWorkspace() {
  return useContext(WorkspaceContext);
}
