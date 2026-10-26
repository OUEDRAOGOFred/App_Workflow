import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  Plus,
  Bot,
  GitBranch,
  Activity,
  CheckCircle2,
  ArrowRight,
  Send,
  BrainCircuit,
  Table2,
  Mail,
} from 'lucide-react';
import { useAll } from '../api/queries';
import type { Agent, Workflow, Execution } from '../api/types';
import {
  Badge,
  date,
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
} from '../components/ui';
export function Dashboard() {
  const agents = useAll<Agent>('/agents', 10000);
  const workflows = useAll<Workflow>('/workflows', 10000);
  const executions = useAll<Execution>('/executions', 10000);
  if (agents.isLoading || workflows.isLoading || executions.isLoading)
    return <Loading />;
  const error = agents.error || workflows.error || executions.error;
  if (error)
    return (
      <ErrorBox
        error={error}
        retry={() => {
          void agents.refetch();
          void workflows.refetch();
          void executions.refetch();
        }}
      />
    );
  const a = agents.data || [],
    w = workflows.data || [],
    e = executions.data || [];
  const done = e.filter((x) =>
    ['success', 'failed', 'cancelled'].includes(x.status),
  );
  const rate = done.length
    ? Math.round(
        (done.filter((x) => x.status === 'success').length / done.length) * 100,
      )
    : null;
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - 6 + i);
    const jobs = e.filter(
      (x) => new Date(x.created_at).toDateString() === d.toDateString(),
    );
    return {
      label: d.toLocaleDateString('fr-FR', { weekday: 'short' }),
      success: jobs.filter((x) => x.status === 'success').length,
      other: jobs.filter((x) => x.status !== 'success').length,
    };
  });
  const max = Math.max(1, ...days.map((d) => d.success + d.other));
  return (
    <>
      <PageTitle
        eyebrow="VUE D’ENSEMBLE"
        title="Votre atelier, en mouvement"
        description="Vos agents travaillent ensemble. Gardez une vue sur l’essentiel."
        actions={
          <Link className="button primary" to="/workflows/new">
            <Plus size={17} />
            Nouveau workflow
          </Link>
        }
      />
      <div className="stats-grid">
        {[
          {
            label: 'Agents actifs',
            value: a.filter((x) => x.status === 'active').length,
            note: `${a.length} agents au total`,
            Icon: Bot,
          },
          {
            label: 'Workflows',
            value: w.length,
            note: `${w.filter((x) => x.is_active).length} activés`,
            Icon: GitBranch,
          },
          {
            label: 'Exécutions',
            value: e.length,
            note: 'Toutes les exécutions enregistrées',
            Icon: Activity,
          },
          {
            label: 'Taux de réussite',
            value: rate === null ? '—' : `${rate}%`,
            note: done.length
              ? 'Sur les exécutions terminées'
              : 'Aucune exécution terminée',
            Icon: CheckCircle2,
          },
        ].map((s) => (
          <div className="stat-card" key={s.label}>
            <div>
              <span>{s.label}</span>
              <s.Icon size={19} />
            </div>
            <strong>{s.value}</strong>
            <small>{s.note}</small>
          </div>
        ))}
      </div>
      <div className="dashboard-columns">
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>L’activité de votre atelier</h2>
              <p>Exécutions des 7 derniers jours</p>
            </div>
            <span className="chart-legend">
              <i />
              Réussies <i className="muted-dot" />
              Autres
            </span>
          </div>
          <div
            className="bar-chart"
            role="img"
            aria-label="Exécutions quotidiennes sur les sept derniers jours"
          >
            {days.map((d, i) => (
              <div className="chart-column" key={i}>
                <div
                  className="bar-track"
                  title={`${d.label} : ${d.success} réussies, ${d.other} autres`}
                >
                  <div
                    className="bar other"
                    style={{ height: `${(d.other / max) * 100}%` }}
                  />
                  <div
                    className="bar"
                    style={{ height: `${(d.success / max) * 100}%` }}
                  />
                </div>
                <span>{d.label}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="panel inspiration">
          <span className="eyebrow">DES OUTILS QUI SE PARLENT</span>
          <h2>
            Un document reçu.
            <br />
            Cinq actions enchaînées.
          </h2>
          <p>
            Réception, classification, archivage et notification : composez
            votre propre chaîne.
          </p>
          <div className="mini-flow">
            {[Send, BrainCircuit, Table2, Mail].map((Icon, i) => (
              <span key={i}>
                <Icon size={22} />
                {i < 3 && <ArrowRight size={14} />}
              </span>
            ))}
          </div>
          <Link to="/agents/new">
            Créer mon premier agent <ArrowUpRight size={17} />
          </Link>
        </section>
      </div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>Dernières exécutions</h2>
            <p>Le détail de chaque action, au même endroit.</p>
          </div>
          <Link className="text-link" to="/executions">
            Tout voir <ArrowRight size={16} />
          </Link>
        </div>
        {!e.length ? (
          <Empty
            title="Prêt pour votre premier lancement"
            text="Créez un agent, ajoutez-le à un workflow puis lancez une exécution."
            action={
              <Link to="/agents/new" className="button secondary">
                Créer un agent
              </Link>
            }
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Statut</th>
                  <th>Déclencheur</th>
                  <th>Date</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {e.slice(0, 6).map((x) => (
                  <tr key={x.id}>
                    <td>
                      {w.find((y) => y.id === x.workflow_id)?.name ||
                        (x.source === 'agent_test'
                          ? 'Test d’agent'
                          : 'Workflow archivé')}
                    </td>
                    <td>
                      <Badge status={x.status} />
                    </td>
                    <td>{x.source}</td>
                    <td>{date(x.created_at)}</td>
                    <td>
                      <Link
                        aria-label="Voir cette exécution"
                        className="icon-button"
                        to={`/executions/${x.id}`}
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
