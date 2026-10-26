import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, GitBranch, ArrowUpRight, Trash2, Search } from 'lucide-react';
import { api, errorMessage } from '../api/client';
import { useAll } from '../api/queries';
import type { Workflow } from '../api/types';
import { useToasts } from '../stores/session';
import {
  Badge,
  date,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageTitle,
} from '../components/ui';
export function Workflows() {
  const query = useAll<Workflow>('/workflows', 10000),
    [search, setSearch] = useState(''),
    [deleting, setDeleting] = useState<Workflow | null>(null),
    [busy, setBusy] = useState(false);
  const cache = useQueryClient(),
    toast = useToasts((s) => s.push);
  const items = query.data?.filter((w) =>
    w.name.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        title="Vos workflows"
        description="Reliez vos agents et laissez les tâches s’enchaîner."
        actions={
          <Link className="button primary" to="/workflows/new">
            <Plus size={17} />
            Nouveau workflow
          </Link>
        }
      />
      <div className="filter-bar">
        <label className="search-field">
          <Search size={17} />
          <input
            placeholder="Rechercher un workflow…"
            aria-label="Rechercher un workflow"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <span>{items?.length || 0} workflow(s)</span>
      </div>
      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : !items?.length ? (
        <Empty
          title="Votre prochaine automatisation commence ici"
          text="Ajoutez vos agents au canevas, reliez-les et lancez votre workflow."
          action={
            <Link className="button secondary" to="/workflows/new">
              Créer un workflow
            </Link>
          }
        />
      ) : (
        <div className="workflow-grid">
          {items.map((w) => (
            <article key={w.id} className="panel workflow-card">
              <div className="section-heading">
                <span className="workflow-symbol">
                  <GitBranch size={23} />
                </span>
                <Badge status={w.is_active ? 'active' : 'disabled'} />
              </div>
              <Link to={`/workflows/${w.id}`}>
                <h3>{w.name}</h3>
              </Link>
              <p>{w.description || 'Une chaîne d’agents, à votre façon.'}</p>
              <div className="workflow-meta">
                <span>{w.graph.nodes.length} agents</span>
                <span>{w.trigger.type}</span>
                <span>v{w.version}</span>
              </div>
              <div className="card-actions">
                <small>Modifié le {date(w.updated_at)}</small>
                <Link
                  to={`/workflows/${w.id}`}
                  className="icon-button"
                  aria-label={`Ouvrir ${w.name}`}
                >
                  <ArrowUpRight size={17} />
                </Link>
                <button
                  aria-label={`Supprimer ${w.name}`}
                  className="icon-button"
                  onClick={() => setDeleting(w)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {deleting && (
        <Modal
          title="Supprimer ce workflow ?"
          onClose={() => setDeleting(null)}
        >
          <p>
            « {deleting.name} » sera supprimé. Son historique d’exécution reste
            consultable.
          </p>
          <button
            className="button danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api(`/workflows/${deleting.id}`, { method: 'DELETE' });
                await cache.invalidateQueries({ queryKey: ['/workflows'] });
                setDeleting(null);
                toast('Workflow supprimé.');
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
