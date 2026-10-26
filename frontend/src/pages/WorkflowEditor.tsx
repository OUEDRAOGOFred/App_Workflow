import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  addEdge,
  type Connection as FlowConnection,
  type Edge,
} from '@xyflow/react';
import {
  ArrowLeft,
  CheckCheck,
  Save,
  Play,
  Power,
  Plus,
  Trash2,
  GripVertical,
  KeyRound,
} from 'lucide-react';
import { api, post } from '../api/client';
import { useAll } from '../api/queries';
import type {
  Agent,
  Connection,
  Execution,
  Graph,
  Obj,
  Trigger,
  Validation,
  Workflow,
} from '../api/types';
import { useToasts } from '../stores/session';
import {
  Badge,
  ErrorBox,
  JsonEditor,
  Loading,
  TypeIcon,
} from '../components/ui';
import { nodeTypes, type FlowNode } from '../components/FlowCanvas';
import { validateGraph } from '../components/graph';
function serialize(nodes: FlowNode[], edges: Edge[]): Graph {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: 'agent',
      position: n.position,
      data: {
        agent_id: n.data.agent_id,
        label: n.data.label,
        input: n.data.input || {},
        timeout_seconds: n.data.timeout_seconds,
        retries: n.data.retries,
      },
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: (e.sourceHandle as 'true' | 'false') || null,
    })),
  };
}
export function WorkflowEditor() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}
function Editor() {
  const { id } = useParams();
  const isNew = !id || id === 'new';
  const agents = useAll<Agent>('/agents', 5000),
    connections = useAll<Connection>('/connections');
  const [workflow, setWorkflow] = useState<Workflow | null>(null),
    [name, setName] = useState('Mon nouveau workflow'),
    [description, setDescription] = useState(''),
    [trigger, setTrigger] = useState<Trigger>({ type: 'manual' }),
    [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]),
    [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]),
    [selected, setSelected] = useState(''),
    [loaded, setLoaded] = useState(isNew),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null),
    [issues, setIssues] = useState<string[]>([]),
    [payload, setPayload] = useState<Obj>({
      text: 'Facture de prestation, total 150 euros.',
    }),
    [validPayload, setValidPayload] = useState(true),
    [mappingValid, setMappingValid] = useState(true),
    [webhookSecret, setWebhookSecret] = useState('');
  const flow = useReactFlow<FlowNode>(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    toast = useToasts((s) => s.push);
  const loadedId = useRef('');
  useEffect(() => {
    if (isNew || loadedId.current === id) return;
    let alive = true;
    setLoaded(false);
    void api<Workflow>(`/workflows/${id}`)
      .then((w) => {
        if (!alive) return;
        loadedId.current = w.id;
        setWorkflow(w);
        setName(w.name);
        setDescription(w.description);
        setTrigger(w.trigger);
        setNodes(w.graph.nodes);
        setEdges(
          w.graph.edges.map((e) => ({
            ...e,
            sourceHandle: e.sourceHandle || undefined,
            type: 'smoothstep',
          })),
        );
        setDirty(false);
        setLoaded(true);
        setTimeout(() => void flow.fitView({ padding: 0.2 }), 100);
      })
      .catch((e) => {
        if (alive) {
          setError(e);
          setLoaded(true);
        }
      });
    return () => {
      alive = false;
    };
  }, [id, isNew, setNodes, setEdges, flow]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const onConnect = useCallback(
    (c: FlowConnection) => {
      setEdges((e) =>
        addEdge(
          { ...c, id: `e_${crypto.randomUUID()}`, type: 'smoothstep' },
          e,
        ),
      );
      setDirty(true);
    },
    [setEdges],
  );
  const current = nodes.find((n) => n.id === selected);
  const active = agents.data?.filter((a) => a.status === 'active') || [];
  function addAgent(agent: Agent, position?: { x: number; y: number }) {
    const node: FlowNode = {
      id: `n_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
      type: 'agent',
      position: position || { x: 100 + nodes.length * 250, y: 180 },
      data: {
        agent_id: agent.id,
        label: agent.name,
        input: {},
        timeout_seconds: 60,
        retries: 0,
      },
    };
    setNodes((n) => [...n, node]);
    setSelected(node.id);
    setDirty(true);
    setMappingValid(true);
  }
  function updateNode(values: Partial<FlowNode['data']>) {
    setNodes((all) =>
      all.map((n) =>
        n.id === selected ? { ...n, data: { ...n.data, ...values } } : n,
      ),
    );
    setDirty(true);
  }
  async function save() {
    if (!name.trim()) throw new Error('Donnez un nom au workflow.');
    if (!mappingValid) throw new Error('Corrigez le mapping JSON.');
    const graph = serialize(nodes, edges);
    if (workflow && !dirty) return workflow;
    const body = { name, description, graph, trigger };
    const result = workflow
      ? await api<Workflow>(`/workflows/${workflow.id}`, {
          method: 'PUT',
          body: { ...body, version: workflow.version },
        })
      : await post<Workflow>('/workflows', body);
    setWorkflow(result);
    setDirty(false);
    loadedId.current = result.id;
    await cache.invalidateQueries({ queryKey: ['/workflows'] });
    if (isNew) navigate(`/workflows/${result.id}`, { replace: true });
    return result;
  }
  async function perform(
    action: 'save' | 'validate' | 'run' | 'activate' | 'deactivate' | 'webhook',
  ) {
    setBusy(true);
    setError(null);
    setIssues([]);
    try {
      if (
        action !== 'save' &&
        action !== 'deactivate' &&
        action !== 'webhook'
      ) {
        const errors = validateGraph(serialize(nodes, edges));
        if (errors.length) {
          setIssues(errors);
          return;
        }
      }
      const saved = await save();
      if (action === 'save') {
        toast('Workflow enregistré.');
        return;
      }
      if (action === 'webhook') {
        const key = await post<{ secret: string }>(
          `/workflows/${saved.id}/webhook-key`,
        );
        setWebhookSecret(key.secret);
        toast('Nouvelle clé webhook créée.');
        return;
      }
      if (action === 'deactivate') {
        const w = await post<Workflow>(`/workflows/${saved.id}/deactivate`);
        setWorkflow(w);
        toast('Workflow désactivé.');
        return;
      }
      const report = await post<Validation>(`/workflows/${saved.id}/validate`);
      if (!report.valid) {
        setIssues(
          report.errors.map(
            (e) => `${e.node_id ? `${e.node_id} : ` : ''}${e.detail}`,
          ),
        );
        return;
      }
      if (action === 'validate') {
        toast('Graphe valide : votre workflow est prêt.');
        return;
      }
      if (action === 'run') {
        const job = await post<Execution>(`/workflows/${saved.id}/run`, {
          payload,
        });
        navigate(`/executions/${job.id}`);
        return;
      }
      setWorkflow(await post<Workflow>(`/workflows/${saved.id}/activate`));
      toast('Workflow activé.');
      await cache.invalidateQueries({ queryKey: ['/workflows'] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (!loaded) return <Loading />;
  if (!isNew && !workflow && error) return <ErrorBox error={error} />;
  const rendered = nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      type_key: agents.data?.find((a) => a.id === n.data.agent_id)?.type_key,
    },
  }));
  return (
    <div className="editor-page">
      <div className="editor-top">
        <div className="editor-name">
          <Link
            to="/workflows"
            className="icon-button"
            aria-label="Tous les workflows"
          >
            <ArrowLeft size={18} />
          </Link>
          <div>
            <input
              aria-label="Nom du workflow"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setDirty(true);
              }}
              maxLength={120}
            />
            <small>
              {workflow ? `Version ${workflow.version}` : 'Nouveau workflow'} ·{' '}
              {dirty ? 'Modifications non enregistrées' : 'Enregistré'}{' '}
              {workflow && (
                <Badge status={workflow.is_active ? 'active' : 'disabled'} />
              )}
            </small>
          </div>
        </div>
        <div className="actions">
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void perform('save')}
          >
            <Save size={16} />
            Enregistrer
          </button>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void perform('validate')}
          >
            <CheckCheck size={16} />
            Valider
          </button>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() =>
              void perform(
                workflow?.is_active && !dirty ? 'deactivate' : 'activate',
              )
            }
          >
            <Power size={16} />
            {workflow?.is_active && !dirty ? 'Désactiver' : 'Activer'}
          </button>
          <button
            className="button primary"
            disabled={busy || !validPayload}
            onClick={() => void perform('run')}
          >
            <Play size={16} />
            Exécuter
          </button>
        </div>
      </div>
      {error != null && <ErrorBox error={error} />}
      {issues.length > 0 && (
        <div className="error-box" role="alert">
          <ul>
            {issues.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="editor-layout">
        <aside className="palette">
          <div className="section-heading">
            <h3>Vos agents</h3>
            <Link
              aria-label="Créer un agent"
              to="/agents/new"
              className="icon-button"
            >
              <Plus size={16} />
            </Link>
          </div>
          <p>Glissez un agent sur le canevas ou cliquez pour l’ajouter.</p>
          {agents.isLoading ? (
            <Loading />
          ) : agents.error ? (
            <ErrorBox error={agents.error} />
          ) : !active.length ? (
            <p>
              Aucun agent actif.{' '}
              <Link to="/agents/new">Créez votre premier agent.</Link>
            </p>
          ) : (
            active.map((a) => (
              <button
                key={a.id}
                className="palette-agent"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/atelier-agent', a.id);
                  e.dataTransfer.effectAllowed = 'copy';
                }}
                onClick={() => addAgent(a)}
              >
                <TypeIcon type={a.type_key} size={17} />
                <span>
                  {a.name}
                  <small>{a.type_key.replaceAll('_', ' ')}</small>
                </span>
                <GripVertical size={15} />
              </button>
            ))
          )}
          <div className="palette-tip">
            <strong>Un lien, une donnée transmise.</strong>
            <p>
              Reliez les poignées des nœuds. Sélectionnez un nœud pour régler
              son entrée.
            </p>
          </div>
        </aside>
        <div className="workflow-canvas" aria-label="Canevas du workflow">
          <ReactFlow<FlowNode>
            nodes={rendered}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={(c) => {
              onNodesChange(c);
              if (c.some((x) => x.type !== 'select' && x.type !== 'dimensions'))
                setDirty(true);
            }}
            onEdgesChange={(c) => {
              onEdgesChange(c);
              if (c.some((x) => x.type !== 'select')) setDirty(true);
            }}
            onConnect={onConnect}
            onNodeClick={(_, n) => {
              setSelected(n.id);
              setMappingValid(true);
            }}
            onPaneClick={() => setSelected('')}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = 'copy';
            }}
            onDrop={(e) => {
              e.preventDefault();
              const a = active.find(
                (a) =>
                  a.id === e.dataTransfer.getData('application/atelier-agent'),
              );
              if (a)
                addAgent(
                  a,
                  flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
                );
            }}
            fitView
            minZoom={0.15}
            deleteKeyCode={['Backspace', 'Delete']}
          >
            <Background gap={22} size={1} />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
          {!nodes.length && (
            <div className="canvas-empty">
              <div>
                <Plus size={25} />
              </div>
              <h3>Tout commence par un agent</h3>
              <p>Ajoutez-le depuis la palette à gauche.</p>
            </div>
          )}
          <span className="canvas-caption">ATELIER / WORKFLOW CANVAS</span>
        </div>
        <aside className="node-panel">
          {current ? (
            <>
              <div className="section-heading">
                <h3>Configurer le nœud</h3>
                <button
                  aria-label="Retirer le nœud"
                  className="icon-button"
                  onClick={() => {
                    setNodes((n) => n.filter((x) => x.id !== selected));
                    setEdges((e) =>
                      e.filter(
                        (x) => x.source !== selected && x.target !== selected,
                      ),
                    );
                    setSelected('');
                    setDirty(true);
                    setMappingValid(true);
                  }}
                >
                  <Trash2 size={16} />
                </button>
              </div>
              <code className="node-id">{current.id}</code>
              <label className="field">
                <span>Libellé</span>
                <input
                  value={current.data.label}
                  onChange={(e) => updateNode({ label: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Délai maximal (secondes)</span>
                <input
                  type="number"
                  min={1}
                  max={300}
                  value={current.data.timeout_seconds}
                  onChange={(e) =>
                    updateNode({
                      timeout_seconds: Math.max(
                        1,
                        Math.min(300, Number(e.target.value)),
                      ),
                    })
                  }
                />
              </label>
              <label className="field">
                <span>Nouvelles tentatives</span>
                <input
                  type="number"
                  min={0}
                  max={5}
                  value={current.data.retries}
                  onChange={(e) =>
                    updateNode({
                      retries: Math.max(0, Math.min(5, Number(e.target.value))),
                    })
                  }
                />
              </label>
              <JsonEditor
                key={selected}
                value={current.data.input}
                label="Mapping d’entrée (objet JSON)"
                onChange={(v) => {
                  const valid =
                    !!v && typeof v === 'object' && !Array.isArray(v);
                  setMappingValid(valid);
                  if (valid) updateNode({ input: v as Obj });
                }}
                onValidity={(valid) => {
                  if (!valid) setMappingValid(false);
                }}
              />
              <small>
                Exemple :<br />
                <code>{'{"text":"{{ nodes.source.output.text }}"}'}</code>
              </small>
              <Link
                className="text-link"
                to={`/agents/${current.data.agent_id}/edit`}
              >
                Modifier l’agent d’origine
              </Link>
            </>
          ) : (
            <>
              <h3>Votre workflow</h3>
              <label className="field">
                <span>Description</span>
                <textarea
                  rows={2}
                  value={description}
                  onChange={(e) => {
                    setDescription(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <label className="field">
                <span>Déclencheur</span>
                <select
                  value={trigger.type}
                  onChange={(e) => {
                    setTrigger({ type: e.target.value as Trigger['type'] });
                    setDirty(true);
                  }}
                >
                  {[
                    ['manual', 'Manuel'],
                    ['telegram', 'Message Telegram'],
                    ['gmail', 'Message Gmail'],
                    ['webhook', 'Webhook'],
                    ['cron', 'Planification cron'],
                  ].map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              {trigger.type === 'cron' && (
                <label className="field">
                  <span>Planification (5 champs)</span>
                  <input
                    placeholder="*/5 * * * *"
                    value={trigger.schedule || ''}
                    onChange={(e) => {
                      setTrigger({ ...trigger, schedule: e.target.value });
                      setDirty(true);
                    }}
                  />
                  <small>Fuseau configuré sur le serveur.</small>
                </label>
              )}
              {['telegram', 'gmail'].includes(trigger.type) && (
                <label className="field">
                  <span>Connexion du déclencheur</span>
                  <select
                    value={trigger.connection_id || ''}
                    onChange={(e) => {
                      setTrigger({
                        ...trigger,
                        connection_id: e.target.value || null,
                      });
                      setDirty(true);
                    }}
                  >
                    <option value="">Choisir…</option>
                    {connections.data
                      ?.filter((c) =>
                        trigger.type === 'telegram'
                          ? c.type === 'telegram'
                          : ['google', 'gmail_smtp'].includes(c.type),
                      )
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                  </select>
                  <small>
                    Le premier agent doit utiliser cette même connexion.
                  </small>
                </label>
              )}
              {trigger.type === 'webhook' && (
                <>
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => void perform('webhook')}
                  >
                    <KeyRound size={15} />
                    Créer / renouveler la clé
                  </button>
                  {webhookSecret && (
                    <div className="webhook-secret">
                      <p>
                        Copiez cette clé maintenant. Elle ne sera plus affichée
                        après votre départ.
                      </p>
                      <code>{webhookSecret}</code>
                      <small>
                        POST /api/v1/webhooks/{workflow?.id}
                        <br />
                        En-têtes X-Webhook-Secret et Idempotency-Key requis.
                      </small>
                    </div>
                  )}
                </>
              )}
              <JsonEditor
                value={payload}
                label="Entrée du lancement manuel"
                onChange={(v) => {
                  const valid =
                    !!v && typeof v === 'object' && !Array.isArray(v);
                  setValidPayload(valid);
                  if (valid) setPayload(v as Obj);
                }}
                onValidity={(v) => {
                  if (!v) setValidPayload(false);
                }}
              />
              <div className="help-card">
                <p>
                  L’enregistrement désactive un workflow modifié. Validez-le
                  puis réactivez-le pour ses déclencheurs automatiques.
                </p>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
