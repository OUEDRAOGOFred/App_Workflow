import type { Graph } from '../api/types';
export function validateGraph(graph: Graph): string[] {
  const errors: string[] = [];
  if (!graph.nodes.length) return ['Ajoutez au moins un agent au canevas.'];
  const ids = new Set(graph.nodes.map((n) => n.id));
  if (ids.size !== graph.nodes.length)
    errors.push('Les identifiants de nœuds doivent être uniques.');
  const edgeIds = new Set(graph.edges.map((e) => e.id));
  if (edgeIds.size !== graph.edges.length)
    errors.push('Les identifiants de liens doivent être uniques.');
  const degrees = new Map(graph.nodes.map((n) => [n.id, 0]));
  const adj = new Map(graph.nodes.map((n) => [n.id, [] as string[]]));
  for (const edge of graph.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) {
      errors.push('Un lien référence un nœud absent.');
      continue;
    }
    degrees.set(edge.target, degrees.get(edge.target)! + 1);
    adj.get(edge.source)!.push(edge.target);
  }
  const roots = [...degrees].filter(([, n]) => !n).map(([id]) => id);
  if (roots.length !== 1)
    errors.push('Le graphe doit avoir un seul nœud de départ.');
  let visited = 0;
  const queue = [...roots];
  while (queue.length) {
    const id = queue.shift()!;
    visited++;
    for (const next of adj.get(id) || []) {
      degrees.set(next, degrees.get(next)! - 1);
      if (!degrees.get(next)) queue.push(next);
    }
  }
  if (visited < graph.nodes.length) errors.push('Le graphe contient un cycle.');
  return errors;
}
