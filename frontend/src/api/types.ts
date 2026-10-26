export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Obj = { [key: string]: Json };
export type Schema = {
  additionalProperties?: boolean;
  maxProperties?: number;
  maxItems?: number;
  uniqueItems?: boolean;
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  enum?: string[];
  default?: Json;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  items?: Schema;
};
export type AgentType = {
  key: string;
  label: string;
  description: string;
  icon: string;
  requires_connection: boolean;
  config_schema: Schema;
};
export type AgentData = {
  type_key: string;
  name: string;
  description: string;
  connection_id: string | null;
  config: Obj;
};
export type Agent = AgentData & {
  id: string;
  status: string;
  last_run_at: string | null;
  updated_at: string;
};
export type DraftData = Omit<AgentData, 'type_key'> & {
  type_key: string | null;
};
export type Draft = {
  id: string;
  step: number;
  data: DraftData;
  updated_at: string;
};
export type Connection = {
  id: string;
  type: string;
  name: string;
  status: string;
  is_mock: boolean;
  poll_config: {
    query: string;
    label_ids: string[];
    interval_seconds: number | null;
  };
};
export type Trigger = {
  type: 'manual' | 'telegram' | 'gmail' | 'webhook' | 'cron';
  connection_id?: string | null;
  schedule?: string | null;
};
export type NodeData = {
  agent_id: string;
  label: string;
  input: Obj;
  timeout_seconds: number;
  retries: number;
  [key: string]: unknown;
};
export type GraphNode = {
  id: string;
  type: 'agent';
  position: { x: number; y: number };
  data: NodeData;
};
export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: 'true' | 'false' | null;
};
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };
export type Workflow = {
  id: string;
  name: string;
  description: string;
  version: number;
  is_active: boolean;
  graph: Graph;
  trigger: Trigger;
  updated_at: string;
};
export type Execution = {
  id: string;
  workflow_id: string | null;
  workflow_version: number | null;
  status: string;
  source: string;
  trigger_payload: Obj;
  cancel_requested: boolean;
  error: string | null;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};
export type Step = {
  id: string;
  node_id: string;
  status: string;
  input: Obj;
  output: Obj | null;
  attempt: number;
  error: string | null;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
};
// Older agent-test snapshots omit canvas metadata. Workflow graphs remain strict.
export type ArchivedGraph = {
  nodes: (Omit<GraphNode, 'position' | 'type' | 'data'> & {
    position?: GraphNode['position'];
    type?: 'agent';
    data: Partial<NodeData>;
  })[];
  edges: GraphEdge[];
};
export type ExecutionDetail = Execution & {
  workflow_name: string;
  graph: ArchivedGraph;
  steps: Step[];
};
export type Validation = {
  valid: boolean;
  errors: { code: string; detail: string; node_id: string | null }[];
  order: string[];
};
