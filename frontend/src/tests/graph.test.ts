import { describe, expect, it } from 'vitest';
import { historyNodes, validateGraph } from '../components/graph';
import { decodeEvents } from '../api/stream';
import type { Graph, GraphNode } from '../api/types';
const node = (id: string): GraphNode => ({
  id,
  type: 'agent',
  position: { x: 0, y: 0 },
  data: {
    agent_id: 'agent',
    label: id,
    input: {},
    timeout_seconds: 60,
    retries: 0,
  },
});
describe('validation du canevas', () => {
  it('accepte des branches rejoignant le même nœud', () => {
    const graph: Graph = {
      nodes: ['a', 'b', 'c', 'd'].map(node),
      edges: [
        { id: 'ab', source: 'a', target: 'b', sourceHandle: 'true' },
        { id: 'ac', source: 'a', target: 'c', sourceHandle: 'false' },
        { id: 'bd', source: 'b', target: 'd' },
        { id: 'cd', source: 'c', target: 'd' },
      ],
    };
    expect(validateGraph(graph)).toEqual([]);
  });
  it('rejette un cycle même si un départ distinct existe', () => {
    expect(
      validateGraph({
        nodes: ['a', 'b', 'c'].map(node),
        edges: [
          { id: 'ab', source: 'a', target: 'b' },
          { id: 'bc', source: 'b', target: 'c' },
          { id: 'cb', source: 'c', target: 'b' },
        ],
      }),
    ).toContain('Le graphe contient un cycle.');
  });
  it('rejette plusieurs départs et des références absentes', () => {
    const errors = validateGraph({
      nodes: ['a', 'b'].map(node),
      edges: [{ id: 'bad', source: 'a', target: 'absent' }],
    });
    expect(errors).toHaveLength(2);
  });
  it('explique un canevas vide', () => {
    expect(validateGraph({ nodes: [], edges: [] })).toEqual([
      'Ajoutez au moins un agent au canevas.',
    ]);
  });
});
describe('SSE fragmenté', () => {
  it('conserve la fin incomplète et lit les données multilignes', () => {
    const first = decodeEvents(
      ': heartbeat\r\n\r\nid: 17\r\nevent: step\r\ndata: {"status":\r\ndata: "running"}\r\n\r\nid: 18\nevent: com',
    );
    expect(first.events).toEqual([
      { id: 17, event: 'step', data: { status: 'running' } },
    ]);
    const second = decodeEvents(first.rest + 'plete\ndata: {}\n\n');
    expect(second.events[0]).toEqual({ id: 18, event: 'complete', data: {} });
  });
  it('ignore un événement malformé sans perdre le suivant', () => {
    expect(decodeEvents('data: nope\n\nid: 2\ndata: {}\n\n').events).toEqual([
      { id: 2, event: 'message', data: {} },
    ]);
  });
});

describe('graphes historiques des tests d’agent', () => {
  it('rend un ancien nœud sans position, type ou libellé sans modifier l’archive', () => {
    const graph = {
      nodes: [
        {
          id: 'test',
          data: {
            agent_id: 'test',
            input: {},
            timeout_seconds: 60,
            retries: 0,
          },
        },
      ],
      edges: [],
    };
    const archived = JSON.stringify(graph);
    const nodes = historyNodes(graph, { test: 'failed' });
    expect(nodes[0]).toMatchObject({
      id: 'test',
      type: 'agent',
      position: { x: 0, y: 0 },
      data: { label: 'test', status: 'failed' },
    });
    expect(JSON.stringify(graph)).toBe(archived);
  });
  it('conserve la position et le libellé des workflows et place les anciens nœuds séparément', () => {
    const first = node('a');
    first.position = { x: 180, y: 70 };
    const graph = { nodes: [first, { id: 'b', data: {} }], edges: [] };
    expect(
      historyNodes(graph, { a: 'success', b: 'running' }).map(
        (n) => n.position,
      ),
    ).toEqual([
      { x: 180, y: 70 },
      { x: 280, y: 0 },
    ]);
  });
});
