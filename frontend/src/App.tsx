import {
  useEffect,
  useState,
  lazy,
  Suspense,
  type ReactNode,
  Component,
} from 'react';
import {
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  ArrowUpRight,
  Bot,
  ChevronRight,
  GitBranch,
  LayoutDashboard,
  Link2,
  LogOut,
  Menu,
  Moon,
  Sun,
  X,
  FlaskConical,
} from 'lucide-react';
import { usePreferences, useSession } from './stores/session';
import { useResource } from './api/queries';
import { Loading, Toasts } from './components/ui';
import { Auth } from './pages/Auth';
const Dashboard = lazy(() =>
  import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })),
);
const Agents = lazy(() =>
  import('./pages/Agents').then((m) => ({ default: m.Agents })),
);
const AgentWizard = lazy(() =>
  import('./pages/AgentWizard').then((m) => ({ default: m.AgentWizard })),
);
const Connections = lazy(() =>
  import('./pages/Connections').then((m) => ({ default: m.Connections })),
);
const Workflows = lazy(() =>
  import('./pages/Workflows').then((m) => ({ default: m.Workflows })),
);
const WorkflowEditor = lazy(() =>
  import('./pages/WorkflowEditor').then((m) => ({ default: m.WorkflowEditor })),
);
const Executions = lazy(() =>
  import('./pages/Executions').then((m) => ({ default: m.Executions })),
);
const ExecutionPage = lazy(() =>
  import('./pages/Executions').then((m) => ({ default: m.ExecutionPage })),
);
const nav = [
  { to: '/', label: 'Vue d’ensemble', Icon: LayoutDashboard },
  { to: '/agents', label: 'Agents', Icon: Bot },
  { to: '/workflows', label: 'Workflows', Icon: GitBranch },
  { to: '/executions', label: 'Exécutions', Icon: Activity },
  { to: '/connections', label: 'Connexions', Icon: Link2 },
];
function Guard() {
  const { ready, authenticated } = useSession();
  if (!ready) return <Loading />;
  return authenticated ? <Shell /> : <Navigate to="/login" replace />;
}
function Shell() {
  const cache = useQueryClient(),
    { dark, toggle } = usePreferences(),
    [mobile, setMobile] = useState(false);
  const location = useLocation();
  const me = useResource<{ email: string }>('/auth/me');
  const integration = useResource<{ mock_integrations: boolean }>(
    '/integrations/status',
    undefined,
    30000,
  );
  const current = nav.find((n) =>
    n.to === '/'
      ? location.pathname === '/'
      : location.pathname.startsWith(n.to),
  );
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobile ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <NavLink className="brand" to="/">
            <span className="brand-mark">
              <GitBranch size={21} />
            </span>
            atelier<span className="brand-dot">.</span>
          </NavLink>
          <button
            className="icon-button mobile-close"
            aria-label="Fermer le menu"
            onClick={() => setMobile(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-label">
          <span>A</span>
          <div>
            Mon espace<small>Workflows d’agents</small>
          </div>
          <ChevronRight size={14} />
        </div>
        <span className="nav-heading">ESPACE DE TRAVAIL</span>
        <nav>
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              onClick={() => setMobile(false)}
              end={n.to === '/'}
              className={({ isActive }) =>
                `nav-item ${isActive ? 'active' : ''}`
              }
            >
              <n.Icon size={18} />
              {n.label}
              {n.to === '/workflows' && <span className="nav-tag">FLOW</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-tip">
            <span className="tip-icon">✦</span>
            <strong>
              Moins de répétition.
              <br />
              Plus de création.
            </strong>
            <p>Confiez une tâche à votre prochain agent.</p>
            <NavLink to="/agents/new">
              Créer un agent <ArrowUpRight size={15} />
            </NavLink>
          </div>
          <button className="theme-button" onClick={toggle}>
            {dark ? <Sun size={17} /> : <Moon size={17} />}Mode{' '}
            {dark ? 'clair' : 'sombre'}
          </button>
          <div className="profile">
            <span className="avatar">
              {me.data?.email.charAt(0).toUpperCase() || 'A'}
            </span>
            <div>
              <strong>Mon compte</strong>
              <small>{me.data?.email || 'Chargement…'}</small>
            </div>
            <button
              aria-label="Se déconnecter"
              className="icon-button"
              onClick={() => {
                cache.clear();
                useSession.getState().clear();
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      {mobile && (
        <button
          className="mobile-scrim"
          aria-label="Fermer le menu"
          onClick={() => setMobile(false)}
        />
      )}
      <div className="main-area">
        <header className="topbar">
          <div>
            <button
              className="icon-button mobile-toggle"
              aria-label="Ouvrir le menu"
              onClick={() => setMobile(true)}
            >
              <Menu size={21} />
            </button>
            <span>Mon espace</span>
            <ChevronRight size={14} />
            <strong>{current?.label || 'Atelier'}</strong>
          </div>
          <div>
            <span className="workspace-status">
              <i />
              Atelier personnel
            </span>
            <a
              href="/docs"
              target="_blank"
              rel="noreferrer"
              className="docs-link"
            >
              API <ArrowUpRight size={14} />
            </a>
          </div>
        </header>
        {integration.data?.mock_integrations && (
          <div className="demo-banner">
            <FlaskConical size={15} />
            Mode démonstration — les connecteurs simulent leurs actions
            externes.
          </div>
        )}
        <main
          className={`page-content ${location.pathname.startsWith('/workflows/') ? 'canvas-page' : ''}`}
        >
          <Outlet />
        </main>
        <footer className="app-footer">
          <span>ATELIER · WORKFLOWS D’AGENTS</span>
          <span>Des connexions aux actions.</span>
        </footer>
      </div>
    </div>
  );
}
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="empty">
        <h1>L’interface a rencontré un problème.</h1>
        <button
          className="button primary"
          onClick={() => window.location.reload()}
        >
          Recharger
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
export default function App() {
  const dark = usePreferences((s) => s.dark);
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
  }, [dark]);
  return (
    <ErrorBoundary>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/login" element={<Auth />} />
          <Route path="/register" element={<Auth register />} />
          <Route element={<Guard />}>
            <Route index element={<Dashboard />} />
            <Route path="agents" element={<Agents />} />
            <Route path="agents/new" element={<AgentWizard />} />
            <Route path="agents/:id/edit" element={<AgentWizard />} />
            <Route path="connections" element={<Connections />} />
            <Route path="workflows" element={<Workflows />} />
            <Route path="workflows/new" element={<WorkflowEditor />} />
            <Route path="workflows/:id" element={<WorkflowEditor />} />
            <Route path="executions" element={<Executions />} />
            <Route path="executions/:id" element={<ExecutionPage />} />
            <Route
              path="*"
              element={
                <div className="empty">
                  <h1>Page introuvable</h1>
                  <NavLink to="/">Retour au dashboard</NavLink>
                </div>
              }
            />
          </Route>
        </Routes>
      </Suspense>
      <Toasts />
    </ErrorBoundary>
  );
}
