import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowUpRight,
  Clock3,
  Square,
  Radio,
  ChevronDown,
} from 'lucide-react';
import { api, post, errorMessage } from '../api/client';
import { useAll } from '../api/queries';
import { followExecution, type StreamEvent } from '../api/stream';
import type { Execution, ExecutionDetail, Workflow } from '../api/types';
import { useToasts } from '../stores/session';
import {
  Badge,
  date,
  Empty,
  ErrorBox,
  JsonView,
  Loading,
  PageTitle,
} from '../components/ui';
import { HistoryCanvas } from '../components/FlowCanvas';
export function Executions() {
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '',
    workflow = params.get('workflow_id') || '';
  const jobs = useAll<Execution>(
    `/executions?status=${encodeURIComponent(status)}${workflow ? `&workflow_id=${encodeURIComponent(workflow)}` : ''}`
      .replace('status=&', '')
      .replace(/\?status=$/, ''),
    5000,
  );
  const workflows = useAll<Workflow>('/workflows');
  function filter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }
  return (
    <>
      <PageTitle
        title="Les exécutions"
        description="Chaque lancement, chaque étape, chaque résultat."
      />
      <div className="filter-bar">
        <select
          aria-label="Statut d’exécution"
          value={status}
          onChange={(e) => filter('status', e.target.value)}
        >
          <option value="">Tous les statuts</option>
          {[
            ['queued', 'En attente'],
            ['running', 'En cours'],
            ['success', 'Réussies'],
            ['failed', 'En échec'],
            ['cancelled', 'Annulées'],
          ].map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select
          aria-label="Workflow"
          value={workflow}
          onChange={(e) => filter('workflow_id', e.target.value)}
        >
          <option value="">Tous les workflows</option>
          {workflows.data?.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        <span className="live-dot">Actualisation · 5 s</span>
      </div>
      <section className="panel">
        {jobs.isLoading ? (
          <Loading />
        ) : jobs.error ? (
          <ErrorBox error={jobs.error} retry={() => void jobs.refetch()} />
        ) : !jobs.data?.length ? (
          <Empty
            title="Aucune exécution pour le moment"
            text="Lancez un workflow ou testez un agent pour suivre ses étapes ici."
            action={
              <Link to="/workflows" className="button secondary">
                Voir mes workflows
              </Link>
            }
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Workflow / test</th>
                  <th>Statut</th>
                  <th>Source</th>
                  <th>Lancement</th>
                  <th>Durée</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((x) => (
                  <tr key={x.id}>
                    <td>
                      <Link to={`/executions/${x.id}`}>
                        {workflows.data?.find((w) => w.id === x.workflow_id)
                          ?.name ||
                          (x.source === 'agent_test'
                            ? 'Test d’agent'
                            : 'Workflow archivé')}
                      </Link>
                      <small className="table-id">
                        {x.id.slice(0, 8)} ·{' '}
                        {x.workflow_version ? `v${x.workflow_version}` : 'test'}
                      </small>
                    </td>
                    <td>
                      <Badge status={x.status} />
                    </td>
                    <td>{x.source}</td>
                    <td>{date(x.created_at)}</td>
                    <td>{duration(x)}</td>
                    <td>
                      <Link
                        aria-label={`Voir l’exécution ${x.id.slice(0, 8)}`}
                        to={`/executions/${x.id}`}
                        className="icon-button"
                      >
                        <ArrowUpRight size={17} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
function duration(x: Execution) {
  return x.started_at
    ? `${Math.max(0, Math.round(((x.finished_at ? new Date(x.finished_at).getTime() : Date.now()) - new Date(x.started_at).getTime()) / 1000))} s`
    : '—';
}
export function ExecutionPage() {
  const { id } = useParams();
  const cache = useQueryClient(),
    toast = useToasts((s) => s.push);
  const [events, setEvents] = useState<{
      id: string | undefined;
      items: StreamEvent[];
    }>({ id, items: [] }),
    [live, setLive] = useState(false),
    [busy, setBusy] = useState(false);
  const job = useQuery({
    queryKey: ['execution', id],
    queryFn: ({ signal }) =>
      api<ExecutionDetail>(`/executions/${id}`, { signal }),
    refetchInterval: (q) =>
      q.state.data &&
      ['success', 'failed', 'cancelled'].includes(q.state.data.status)
        ? false
        : 2000,
  });
  useEffect(() => {
    if (!id) return;
    const abort = new AbortController();
    void followExecution(
      id,
      (evt) => {
        if (abort.signal.aborted) return;
        setEvents((all) => ({
          id,
          items: [
            ...(all.id === id ? all.items : []).filter((e) => e.id !== evt.id),
            evt,
          ].slice(-250),
        }));
        void cache.invalidateQueries({ queryKey: ['execution', id] });
      },
      abort.signal,
      (v) => {
        if (!abort.signal.aborted) setLive(v);
      },
    );
    return () => abort.abort();
  }, [id, cache]);
  if (job.isLoading) return <Loading />;
  if (job.error)
    return <ErrorBox error={job.error} retry={() => void job.refetch()} />;
  if (!job.data) return null;
  const x = job.data;
  const terminal = ['success', 'failed', 'cancelled'].includes(x.status);
  return (
    <>
      <Link className="back-link" to="/executions">
        <ArrowLeft size={16} />
        Toutes les exécutions
      </Link>
      <PageTitle
        eyebrow={`EXÉCUTION / ${x.id.slice(0, 8).toUpperCase()}`}
        title={x.workflow_name}
        description={`${date(x.created_at)} · ${x.source}${x.workflow_version ? ` · version ${x.workflow_version}` : ''}`}
        actions={
          <>
            <Badge status={x.status} />
            {!terminal && (
              <button
                disabled={busy || x.cancel_requested}
                className="button secondary"
                onClick={async () => {
                  setBusy(true);
                  try {
                    await post(`/executions/${id}/cancel`);
                    await job.refetch();
                    toast('Annulation demandée.');
                  } catch (e) {
                    toast(errorMessage(e), 'error');
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <Square size={14} />
                {x.cancel_requested ? 'Annulation demandée' : 'Annuler'}
              </button>
            )}
          </>
        }
      />
      <div className="execution-summary">
        <span>
          <Clock3 size={16} />
          Durée : {duration(x)}
        </span>
        <span>
          {x.steps.filter((s) => s.status === 'success').length} /{' '}
          {x.steps.length} étapes réussies
        </span>
        <span className={live ? 'live-dot' : ''}>
          <Radio size={15} />
          {live
            ? 'Logs en direct'
            : terminal
              ? 'Exécution terminée'
              : 'Reconnexion au flux…'}
        </span>
      </div>
      {x.error && (
        <ErrorBox error={`${x.error_code || 'Erreur'} : ${x.error}`} />
      )}
      <HistoryCanvas
        graph={x.graph}
        statuses={Object.fromEntries(x.steps.map((s) => [s.node_id, s.status]))}
      />
      <div className="execution-columns">
        <section className="panel">
          <div className="section-heading">
            <h2>Étape par étape</h2>
            <span>{x.steps.length} nœuds</span>
          </div>
          {x.steps.map((step, i) => (
            <details
              key={step.id}
              className="step-detail"
              open={
                step.status === 'failed' ||
                step.status === 'running' ||
                x.steps.length === 1
              }
            >
              <summary>
                <span className="step-number">{i + 1}</span>
                <strong>
                  {x.graph.nodes.find((n) => n.id === step.node_id)?.data
                    .label || step.node_id}
                </strong>
                <Badge status={step.status} />
                <ChevronDown size={16} />
              </summary>
              <div className="step-content">
                <small>
                  {step.node_id} · {step.attempt} tentative(s) ·{' '}
                  {date(step.started_at)}
                </small>
                {step.error && (
                  <ErrorBox
                    error={`${step.error_code || 'Erreur'} : ${step.error}`}
                  />
                )}
                <h4>Entrée</h4>
                <JsonView value={step.input} />
                <h4>Sortie</h4>
                <JsonView value={step.output} />
              </div>
            </details>
          ))}
        </section>
        <section className="panel execution-logs">
          <div className="section-heading">
            <h2>Journal d’exécution</h2>
            <Radio size={18} />
          </div>
          {!events.items.length ? (
            <p>En attente des événements…</p>
          ) : (
            events.items.map((evt, i) => (
              <div className="log-event" key={`${evt.id}-${i}`}>
                <span>#{evt.id || '—'}</span>
                <div>
                  <strong>{evt.event}</strong>
                  <pre>{JSON.stringify(evt.data, null, 2)}</pre>
                </div>
              </div>
            ))
          )}
          <details>
            <summary>Données du déclencheur</summary>
            <JsonView value={x.trigger_payload} />
          </details>
        </section>
      </div>
    </>
  );
}
