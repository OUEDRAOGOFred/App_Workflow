import {
  Handle,
  Position,
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import type { Graph, NodeData } from '../api/types';
import { Badge, TypeIcon } from './ui';
export type FlowNode = Node<
  NodeData & { type_key?: string; status?: string },
  'agent'
>;
export function AgentNode({ data, selected }: NodeProps<FlowNode>) {
  return (
    <div
      className={`flow-agent ${selected ? 'selected' : ''} ${data.status ? `node-${data.status}` : ''}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flow-agent-icon">
        <TypeIcon type={String(data.type_key || 'agent')} />
      </div>
      <div>
        <small>{String(data.type_key || 'AGENT').replaceAll('_', ' ')}</small>
        <strong>{data.label}</strong>
        {data.status && <Badge status={data.status} />}
      </div>
      {data.type_key === 'condition' ? (
        <>
          <span className="handle-label handle-true">Oui</span>
          <Handle
            type="source"
            id="true"
            position={Position.Right}
            style={{ top: '35%' }}
          />
          <span className="handle-label handle-false">Non</span>
          <Handle
            type="source"
            id="false"
            position={Position.Right}
            style={{ top: '75%' }}
          />
        </>
      ) : (
        <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}
export const nodeTypes = { agent: AgentNode };
export function HistoryCanvas({
  graph,
  statuses,
}: {
  graph: Graph;
  statuses: Record<string, string>;
}) {
  return (
    <div className="history-canvas">
      <ReactFlowProvider>
        <ReactFlow<FlowNode>
          nodes={graph.nodes.map((n) => ({
            ...n,
            data: { ...n.data, status: statuses[n.id] },
          }))}
          edges={graph.edges.map((e) => ({
            ...e,
            sourceHandle: e.sourceHandle || undefined,
            type: 'smoothstep',
          }))}
          nodeTypes={nodeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          fitView
          minZoom={0.2}
        >
          <Background gap={20} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
