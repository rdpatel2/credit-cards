"use client";
import { useEffect, useSyncExternalStore } from "react";
import {
  initialWorkspace,
  parseWorkspace,
  STORAGE_KEY,
  type Workspace,
} from "./workspace";
type Snapshot = { workspace: Workspace | null; error: string | null };
const server: Snapshot = { workspace: null, error: null };
let snapshot: Snapshot = server;
const listeners = new Set<() => void>();
function emit() {
  listeners.forEach((fn) => fn());
}
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    snapshot = {
      workspace: raw ? parseWorkspace(raw) : initialWorkspace(),
      error: null,
    };
  } catch {
    snapshot = {
      workspace: initialWorkspace(),
      error:
        "Saved workspace could not be read. Defaults are loaded; export any important work before replacing your saved data.",
    };
  }
  emit();
}
export function updateWorkspace(
  change: Workspace | ((state: Workspace) => Workspace),
) {
  const workspace =
    typeof change === "function"
      ? change(snapshot.workspace ?? initialWorkspace())
      : change;
  let error: string | null = null;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(workspace));
  } catch {
    error =
      "Browser storage is unavailable or full. Changes are only kept in this tab; export your workspace to save them.";
  }
  snapshot = { workspace, error };
  emit();
}
export function useWorkspace() {
  const state = useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    () => snapshot,
    () => server,
  );
  useEffect(() => {
    if (!snapshot.workspace) load();
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === null) load();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  return { ...state, update: updateWorkspace };
}
