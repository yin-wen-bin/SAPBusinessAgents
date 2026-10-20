/** Business references, not DOM selectors, are the only submitted annotation targets. */
export type AnnotationItem<R> = { key: string; label: string; reference: R; comment: string };
export type AnnotationHost<R> = {
  scopeKey: string; revision: number; root: () => Element | null;
  resolve: (element: Element) => (Omit<AnnotationItem<R>, "comment"> & { element?: Element }) | null;
  find: (reference: R) => Element | null;
  reveal?: (reference: R) => void;
  stale: (reference: R) => boolean;
};
export const annotationLimit = 20;
export const annotationCommentLimit = 2000;

export function upsertAnnotation<R>(items: AnnotationItem<R>[], item: AnnotationItem<R>): AnnotationItem<R>[] {
  const index = items.findIndex(entry => entry.key === item.key);
  if (index < 0 && items.length >= annotationLimit) throw new Error("annotation_limit");
  const comment = item.comment.trim();
  if (!comment || comment.length > annotationCommentLimit) throw new Error("annotation_comment_invalid");
  return index < 0 ? [...items, { ...item, comment }] : items.map((entry, i) => i === index ? { ...item, comment } : entry);
}

export function annotationFeedback(text: string, count: number, locale: string): string {
  return text.trim() || (locale === "zh" ? `请根据这 ${count} 处标注意见修改当前已保存的定义。` : `Revise the saved definition using these ${count} annotation comments.`);
}

export type WorkflowAnnotationReference = { kind: "workflow_field"; draftId: string; revision: number; path: string;
  objectId?: string; objectKind?: "node" | "connection" };

/** Resolve a stable object ID against this revision; never silently follow an array index. */
export function workflowAnnotationReference(workflow: any, draftId: string, revision: number,
  target: { path?: string; nodeId?: string; connectionId?: string }, connectionKey: (value: any) => string): WorkflowAnnotationReference | null {
  const base = { kind: "workflow_field" as const, draftId, revision };
  if (target.nodeId) {
    const index = workflow.nodes.findIndex((node: any) => node.id === target.nodeId);
    return index < 0 || workflow.nodes.filter((node: any) => node.id === target.nodeId).length !== 1 ? null
      : { ...base, path: `/nodes/${index}`, objectId: target.nodeId, objectKind: "node" };
  }
  if (target.connectionId) {
    const index = workflow.connections.findIndex((entry: any) => connectionKey(entry) === target.connectionId);
    return index < 0 || workflow.connections.filter((entry: any) => connectionKey(entry) === target.connectionId).length !== 1 ? null
      : { ...base, path: `/connections/${index}`, objectId: target.connectionId, objectKind: "connection" };
  }
  if (!target.path || !/^\/(title|description|inputSchema|outputSchema|outputs|nodes|connections)(\/|$)/.test(target.path)) return null;
  try {
    let value = workflow;
    for (const token of target.path.slice(1).split("/")) value = value[token.replace(/~1/g, "/").replace(/~0/g, "~")];
    if (value === undefined) return null;
  } catch { return null; }
  return { ...base, path: target.path };
}
