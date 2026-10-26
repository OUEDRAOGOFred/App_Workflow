import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  Search,
  Play,
  Pencil,
  Power,
  Trash2,
  ArrowRight,
} from 'lucide-react';
import { api, post, errorMessage } from '../api/client';
import { useAll, useResource } from '../api/queries';
import type { Agent, AgentType, Draft, Execution, Obj } from '../api/types';
import { useToasts } from '../stores/session';
import {
  Badge,
  date,
  Empty,
  ErrorBox,
  JsonEditor,
  Loading,
  Modal,
  PageTitle,
  TypeIcon,
} from '../components/ui';
export function Agents() {
  const agents = useAll<Agent>('/agents', 5000),
    types = useResource<AgentType[]>('/agent-types'),
    drafts = useAll<Draft>('/agent-drafts');
  const [search, setSearch] = useState(''),
    [status, setStatus] = useState(''),
    [type, setType] = useState(''),
    [busy, setBusy] = useState(false),
    [test, setTest] = useState<Agent | null>(null),
    [input, setInput] = useState<Obj>({
      text: 'Facture de prestation, montant total 150 euros.',
    }),
    [valid, setValid] = useState(true),
    [deleting, setDeleting] = useState<Agent | null>(null);
  const cache = useQueryClient(),
    navigate = useNavigate(),
    toast = useToasts((s) => s.push);
  async function action(agent: Agent, kind: string) {
    setBusy(true);
    try {
      await post(`/agents/${agent.id}/${kind}`);
      await cache.invalidateQueries({ queryKey: ['/agents'] });
      toast('État de l’agent mis à jour.');
    } catch (e) {
      toast(errorMessage(e), 'error');
    } finally {
      setBusy(false);
    }
  }
  async function run() {
    if (!test) return;
    setBusy(true);
    try {
      const job = await post<Execution>(`/agents/${test.id}/test`, { input });
      navigate(`/executions/${job.id}`);
    } catch (e) {
      toast(errorMessage(e), 'error');
    } finally {
      setBusy(false);
    }
  }
  const filtered = (agents.data || []).filter(
    (a) =>
      (!status || a.status === status) &&
      (!type || a.type_key === type) &&
      `${a.name} ${a.description}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        title="Vos agents"
        description="Une tâche, un agent. Ensemble, ils font avancer vos idées."
        actions={
          <Link className="button primary" to="/agents/new">
            <Plus size={18} />
            Créer un agent
          </Link>
        }
      />
      {drafts.error && <ErrorBox error={drafts.error} />}{' '}
      {!!drafts.data?.length && (
        <div className="draft-strip">
          <span>{drafts.data.length} brouillon(s) à reprendre</span>
          {drafts.data.slice(0, 4).map((d) => (
            <Link key={d.id} to={`/agents/new?draft=${d.id}`}>
              {d.data.name || 'Agent sans nom'} <ArrowRight size={14} />
            </Link>
          ))}
        </div>
      )}
      <div className="filter-bar">
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Rechercher un agent"
            placeholder="Rechercher un agent…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <select
          aria-label="Filtrer par statut"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">Tous les statuts</option>
          <option value="active">Actifs</option>
          <option value="draft">Brouillons</option>
          <option value="error">En erreur</option>
          <option value="disabled">Inactifs</option>
        </select>
        <select
          aria-label="Filtrer par type"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="">Tous les types</option>
          {types.data?.map((t) => (
            <option value={t.key} key={t.key}>
              {t.label}
            </option>
          ))}
        </select>
        <span className="live-dot">Actualisation · 5 s</span>
      </div>
      {agents.isLoading ? (
        <Loading />
      ) : agents.error ? (
        <ErrorBox error={agents.error} retry={() => void agents.refetch()} />
      ) : !filtered.length ? (
        <Empty
          title="Aucun agent à afficher"
          text={
            agents.data?.length
              ? 'Essayez un autre filtre.'
              : 'Commencez par choisir un type et configurez votre premier agent.'
          }
          action={
            <Link to="/agents/new" className="button secondary">
              Créer un agent
            </Link>
          }
        />
      ) : (
        <div className="agent-grid">
          {filtered.map((a) => (
            <article key={a.id} className="agent-card">
              <div className="agent-card-top">
                <TypeIcon type={a.type_key} size={23} />
                <Badge status={a.status} />
              </div>
              <h3>{a.name}</h3>
              <span className="agent-kind">
                {types.data?.find((t) => t.key === a.type_key)?.label ||
                  a.type_key}
              </span>
              <p>
                {a.description ||
                  'Votre agent est prêt à rejoindre un workflow.'}
              </p>
              <div className="agent-last">
                Dernière exécution <span>{date(a.last_run_at)}</span>
              </div>
              <div className="card-actions">
                <button
                  disabled={busy}
                  onClick={() => {
                    setTest(a);
                    setValid(true);
                  }}
                >
                  <Play size={15} />
                  Tester
                </button>
                <Link
                  to={`/agents/${a.id}/edit`}
                  aria-label={`Modifier ${a.name}`}
                >
                  <Pencil size={16} />
                </Link>
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(
                      a,
                      a.status === 'active' ? 'deactivate' : 'activate',
                    )
                  }
                  aria-label={`${a.status === 'active' ? 'Désactiver' : 'Activer'} ${a.name}`}
                >
                  <Power size={16} />
                </button>
                <button
                  disabled={busy}
                  onClick={() => setDeleting(a)}
                  aria-label={`Supprimer ${a.name}`}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {test && (
        <Modal title={`Tester ${test.name}`} onClose={() => setTest(null)}>
          <p>
            Ce test exécute les actions configurées, y compris les envois si la
            connexion est réelle.
          </p>
          <JsonEditor
            value={input}
            onValidity={setValid}
            label="Entrée du test (objet JSON)"
            onChange={(v) => {
              const ok =
                typeof v === 'object' && v !== null && !Array.isArray(v);
              setValid(ok);
              if (ok) setInput(v as Obj);
            }}
          />
          <button
            disabled={busy || !valid}
            className="button primary"
            onClick={() => void run()}
          >
            <Play size={16} />
            Lancer le test
          </button>
        </Modal>
      )}
      {deleting && (
        <Modal title="Supprimer cet agent ?" onClose={() => setDeleting(null)}>
          <p>
            « {deleting.name} » sera supprimé. Les workflows qui le référencent
            devront être corrigés.
          </p>
          <button
            className="button danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api(`/agents/${deleting.id}`, { method: 'DELETE' });
                setDeleting(null);
                await cache.invalidateQueries({ queryKey: ['/agents'] });
                toast('Agent supprimé.');
              } catch (e) {
                toast(errorMessage(e), 'error');
              } finally {
                setBusy(false);
              }
            }}
          >
            Supprimer
          </button>
        </Modal>
      )}
    </>
  );
}
