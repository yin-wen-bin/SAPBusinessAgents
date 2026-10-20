import { workflowAnnotationReference, type AnnotationHost, type WorkflowAnnotationReference } from "./assistantAnnotations.ts";

export const workflowConnectionKey = (item: any): string => {
  const from = item.from.scope === "node_output" ? `${item.from.nodeId}:${item.from.port}` : item.from.scope === "iteration_item"
    ? `iteration:${item.from.pointer ?? "/"}` : `${item.from.scope}:${item.from.port ?? "value"}`;
  return `${from}->${item.to.nodeId}:${item.to.port}`;
};
const selector = "[data-workflow-ref], [data-workflow-node], [data-workflow-connection], .react-flow__node, .react-flow__edge";

/** Page mapping only. Submission, authorization and compilation remain on the workflow service. */
export function workflowAnnotationHost({ workflow, draftId, revision, root, reveal }: {
  workflow: any; draftId: string; revision: number; root: () => Element | null;
  reveal: (reference: WorkflowAnnotationReference) => void;
}): AnnotationHost<WorkflowAnnotationReference> {
  const key = (ref: WorkflowAnnotationReference) => `${ref.draftId}:${ref.objectKind || "field"}:${ref.objectId || ""}:${ref.path}`;
  const resolve = (element: Element) => {
    const target = element.closest(selector) as HTMLElement | null;
    if (!target || !root()?.contains(target)) return null;
    let reference: WorkflowAnnotationReference | null;
    if (target.dataset.workflowRef) {
      reference = workflowAnnotationReference(workflow, draftId, revision, { path: target.dataset.workflowRef }, workflowConnectionKey);
      const indexed = /^\/(nodes|connections)\/(0|[1-9][0-9]*)(?:\/|$)/.exec(target.dataset.workflowRef);
      if (reference && indexed) {
        const object = workflow[indexed[1]][Number(indexed[2])];
        reference = { ...reference, objectKind: indexed[1] === "nodes" ? "node" : "connection",
          objectId: indexed[1] === "nodes" ? object.id : workflowConnectionKey(object) };
      }
    } else {
      const nodeId = target.dataset.workflowNode || (target.classList.contains("react-flow__node") ? target.dataset.id : undefined);
      const connectionId = target.dataset.workflowConnection || (target.classList.contains("react-flow__edge") ? target.dataset.id : undefined);
      reference = workflowAnnotationReference(workflow, draftId, revision, { nodeId, connectionId }, workflowConnectionKey);
    }
    if (!reference) return null;
    const label = target.dataset.assistantLabel || target.querySelector("h2,h3,strong,span")?.textContent?.trim()
      || reference.objectId || reference.path;
    return { key: key(reference), label: label.slice(0, 100), reference, element: target };
  };
  return { scopeKey: draftId, revision, root, resolve, reveal,
    stale: reference => {
      if (reference.draftId !== draftId || reference.revision !== revision) return true;
      const match = /^\/(nodes|connections)\/(0|[1-9][0-9]*)(?:\/|$)/.exec(reference.path);
      if (reference.objectId && match) {
        const row = workflow[match[1]][Number(match[2])];
        return !row || (reference.objectKind === "node" ? row.id : workflowConnectionKey(row)) !== reference.objectId;
      }
      return workflowAnnotationReference(workflow, draftId, revision, { path: reference.path }, workflowConnectionKey) === null;
    },
    find: reference => [...(root()?.querySelectorAll(selector) || [])].find(element => {
      const candidate = resolve(element);
      return candidate && key(candidate.reference) === key(reference);
    }) || null };
}
