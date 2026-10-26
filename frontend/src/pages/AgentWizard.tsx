import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Play,
  Save,
  LoaderCircle,
} from 'lucide-react';
import { api, post } from '../api/client';
import { useAll, useResource } from '../api/queries';
import type {
  Agent,
  AgentData,
  AgentType,
  Connection,
  Draft,
  DraftData,
  Execution,
  ExecutionDetail,
  Obj,
} from '../api/types';
import { useToasts } from '../stores/session';
import { defaults, SchemaForm, validateConfig } from '../components/SchemaForm';
import {
  Badge,
  ErrorBox,
  JsonEditor,
  JsonView,
  Loading,
  PageTitle,
  TypeIcon,
} from '../components/ui';
import { compatible, ConnectionCreator } from './Connections';
const steps = ['Type d’agent', 'Connexion', 'Configuration', 'Test', 'Résumé'];
const initial: DraftData = {
  type_key: null,
  name: '',
  description: '',
  connection_id: null,
  config: {},
};
export function AgentWizard() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const existingDraft = params.get('draft');
  const navigate = useNavigate(),
    cache = useQueryClient(),
    toast = useToasts((s) => s.push);
  const types = useResource<AgentType[]>('/agent-types'),
    connections = useAll<Connection>('/connections', 5000);
  const [data, setData] = useState<DraftData>(initial),
    [step, setStep] = useState(1),
    [loaded, setLoaded] = useState(!id && !existingDraft),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null),
    [saveState, setSaveState] = useState(''),
    [input, setInput] = useState<Obj>({
      text: 'Facture : prestation de service, total 150 euros.',
    }),
    [inputValid, setInputValid] = useState(true),
    [jobId, setJobId] = useState(''),
    [activate, setActivate] = useState(true);
  const form = useRef<HTMLFormElement>(null);
  const draftId = useRef(existingDraft);
  const savedAgentId = useRef<string | null>(id || null);
  const finalized = useRef(false);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const latest = useRef({ data, step, loaded });
  useEffect(() => {
    latest.current = { data, step, loaded };
  }, [data, step, loaded]);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        if (existingDraft) {
          const d = await api<Draft>(`/agent-drafts/${existingDraft}`);
          if (alive) {
            setData(d.data);
            setStep(d.step);
            setLoaded(true);
          }
        } else if (id) {
          const a = await api<Agent>(`/agents/${id}`);
          if (alive) {
            setData({
              type_key: a.type_key,
              name: a.name,
              description: a.description,
              config: a.config,
              connection_id: a.connection_id,
            });
            setActivate(a.status === 'active');
            setStep(3);
            setLoaded(true);
          }
        }
      } catch (e) {
        if (alive) setError(e);
      }
    }
    void load();
    return () => {
      alive = false;
    };
  }, [id, existingDraft]);
  const persist = useCallback((d: DraftData, s: number) => {
    if (finalized.current) return Promise.resolve('');
    const next = chain.current
      .catch(() => undefined)
      .then(async () => {
        const body = { step: s, data: d };
        const saved = draftId.current
          ? await api<Draft>(`/agent-drafts/${draftId.current}`, {
              method: 'PUT',
              body,
            })
          : await post<Draft>('/agent-drafts', body);
        draftId.current = saved.id;
        return saved.id;
      });
    chain.current = next;
    return next;
  }, []);
  useEffect(() => {
    if (!loaded) return;
    const timer = setTimeout(() => {
      setSaveState('Sauvegarde…');
      void persist(data, step)
        .then(() => setSaveState('Brouillon enregistré'))
        .catch((e) => {
          setSaveState('Sauvegarde impossible');
          setError(e);
        });
    }, 700);
    return () => clearTimeout(timer);
  }, [data, step, loaded, persist]);
  useEffect(
    () => () => {
      const v = latest.current;
      if (v.loaded && v.data.type_key)
        void persist(v.data, v.step).catch(() => undefined);
    },
    [persist],
  );
  const job = useQuery({
    queryKey: ['wizard-test', jobId],
    queryFn: () => api<ExecutionDetail>(`/executions/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) =>
      ['success', 'failed', 'cancelled'].includes(q.state.data?.status || '')
        ? false
        : 1000,
  });
  const type = types.data?.find((t) => t.key === data.type_key);
  const sheets = useQuery({
    queryKey: ['spreadsheets', data.connection_id],
    queryFn: () =>
      api<{ spreadsheets: { id: string; name: string }[] }>(
        `/integrations/sheets/${data.connection_id}/spreadsheets`,
      ),
    enabled:
      step === 3 &&
      !!data.connection_id &&
      !!data.type_key?.startsWith('sheets'),
  });
  const tabs = useQuery({
    queryKey: ['sheet-tabs', data.connection_id, data.config.spreadsheet_id],
    queryFn: () =>
      api<{ sheets: { title: string }[] }>(
        `/integrations/sheets/${data.connection_id}/spreadsheets/${encodeURIComponent(String(data.config.spreadsheet_id))}/sheets`,
      ),
    enabled:
      step === 3 &&
      !!data.connection_id &&
      !!data.type_key?.startsWith('sheets') &&
      typeof data.config.spreadsheet_id === 'string' &&
      !!data.config.spreadsheet_id &&
      !data.config.spreadsheet_id.includes('{{'),
  });
  const labels = useQuery({
    queryKey: ['gmail-labels', data.connection_id],
    queryFn: () =>
      api<{ labels: { id: string; name: string }[] }>(
        `/integrations/gmail/${data.connection_id}/labels`,
      ),
    enabled:
      step === 3 &&
      data.type_key === 'gmail_read' &&
      connections.data?.some(
        (c) => c.id === data.connection_id && c.type === 'google',
      ) === true,
  });
  const configErrors = type
    ? validateConfig(type.config_schema, data.config)
    : [];
  async function next(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (step === 1 && !type) {
      setError(new Error('Choisissez un type d’agent.'));
      return;
    }
    if (step === 2 && type?.requires_connection && !data.connection_id) {
      setError(new Error('Choisissez une connexion.'));
      return;
    }
    if (step === 3 && configErrors.length) {
      setError(new Error(configErrors.join(' · ')));
      return;
    }
    if (step < 5) setStep(step + 1);
    else await finish();
  }
  async function finish() {
    setBusy(true);
    try {
      const body: AgentData = { ...data, type_key: data.type_key! };
      const a = savedAgentId.current
        ? await api<Agent>(`/agents/${savedAgentId.current}`, {
            method: 'PUT',
            body,
          })
        : await post<Agent>('/agents', body);
      savedAgentId.current = a.id;
      if (activate) await post(`/agents/${a.id}/activate`);
      else if (id) await post(`/agents/${a.id}/deactivate`);
      finalized.current = true;
      await chain.current.catch(() => undefined);
      if (draftId.current) {
        await api(`/agent-drafts/${draftId.current}`, { method: 'DELETE' });
        draftId.current = null;
      }
      latest.current.loaded = false;
      await cache.invalidateQueries({ queryKey: ['/agents'] });
      await cache.invalidateQueries({ queryKey: ['/agent-drafts'] });
      toast(id ? 'Agent mis à jour.' : 'Votre agent est prêt.');
      navigate('/agents');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    if (!inputValid) return;
    setBusy(true);
    setError(null);
    try {
      const d = await persist(data, step);
      const execution = await post<Execution>(`/agent-drafts/${d}/test`, {
        input,
      });
      setJobId(execution.id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (types.isLoading || (!loaded && !error)) return <Loading />;
  if (types.error) return <ErrorBox error={types.error} />;
  return (
    <>
      <Link className="back-link" to="/agents">
        <ArrowLeft size={16} />
        Tous les agents
      </Link>
      <PageTitle
        title={id ? 'Modifier votre agent' : 'Donnons vie à votre agent'}
        description="Cinq étapes pour transformer une tâche en automatisation."
        actions={
          <span className="save-state">
            <Save size={15} />
            {saveState}
          </span>
        }
      />
      <div className="wizard-steps" aria-label="Étapes de création">
        {steps.map((label, i) => (
          <div
            key={label}
            className={`wizard-step ${step === i + 1 ? 'current' : step > i + 1 ? 'done' : ''}`}
            aria-current={step === i + 1 ? 'step' : undefined}
          >
            <span>{step > i + 1 ? <Check size={17} /> : i + 1}</span>
            <div>
              <small>ÉTAPE {i + 1}</small>
              <strong>{label}</strong>
            </div>
          </div>
        ))}
      </div>
      <form
        className="wizard-body panel"
        ref={form}
        onSubmit={(e) => void next(e)}
      >
        <div className="section-heading">
          <div>
            <span className="eyebrow">ÉTAPE {step} SUR 5</span>
            <h2>{steps[step - 1]}</h2>
          </div>
          {type && <TypeIcon type={type.key} />}
        </div>
        {error != null && <ErrorBox error={error} />}
        {step === 1 && (
          <>
            <p>Quel rôle voulez-vous confier à votre agent ?</p>
            <div className="type-grid">
              {types.data?.map((t) => (
                <button
                  type="button"
                  key={t.key}
                  aria-pressed={type?.key === t.key}
                  className={`type-card ${type?.key === t.key ? 'selected' : ''}`}
                  onClick={() => {
                    setData({
                      ...data,
                      type_key: t.key,
                      connection_id: null,
                      config: defaults(t.config_schema),
                    });
                    setJobId('');
                  }}
                >
                  <TypeIcon type={t.key} />
                  <strong>{t.label}</strong>
                  <span>{t.description}</span>
                  {type?.key === t.key && (
                    <Check size={17} className="selected-check" />
                  )}
                </button>
              ))}
            </div>
          </>
        )}
        {step === 2 && (
          <>
            {type?.requires_connection ? (
              <>
                <p>Choisissez le compte que cet agent utilisera.</p>
                <label className="field">
                  <span>Connexion</span>
                  <select
                    aria-label="Connexion"
                    required
                    value={data.connection_id || ''}
                    onChange={(e) =>
                      setData({
                        ...data,
                        connection_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">Choisir une connexion…</option>
                    {connections.data
                      ?.filter(
                        (c) => compatible(type.key, c) && c.status === 'active',
                      )
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                          {c.is_mock ? ' · démo' : ''}
                        </option>
                      ))}
                  </select>
                </label>
                <div className="actions">
                  <ConnectionCreator
                    onCreated={(connection_id) => {
                      if (connection_id) setData({ ...data, connection_id });
                    }}
                  />
                  <button
                    type="button"
                    className="button secondary"
                    disabled={!data.connection_id || busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await post(`/connections/${data.connection_id}/test`);
                        toast('Connexion testée.');
                      } catch (e) {
                        setError(e);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Tester la connexion
                  </button>
                </div>
              </>
            ) : (
              <div className="help-card">
                <Check size={22} />
                <h3>Aucune connexion nécessaire</h3>
                <p>
                  Cet agent fonctionne directement avec les services de votre
                  atelier.
                </p>
              </div>
            )}
            {connections.error && <ErrorBox error={connections.error} />}
          </>
        )}
        {step === 3 && type && (
          <>
            <div className="two-fields">
              <label className="field">
                <span>Nom de l’agent *</span>
                <input
                  required
                  maxLength={120}
                  value={data.name}
                  onChange={(e) => setData({ ...data, name: e.target.value })}
                  placeholder="Ex. Classifier mes documents"
                />
              </label>
              <label className="field">
                <span>Description</span>
                <input
                  maxLength={4000}
                  value={data.description}
                  onChange={(e) =>
                    setData({ ...data, description: e.target.value })
                  }
                  placeholder="Son rôle dans votre workflow"
                />
              </label>
            </div>
            {(sheets.error || tabs.error || labels.error) && (
              <ErrorBox error={sheets.error || tabs.error || labels.error} />
            )}
            <SchemaForm
              key={type.key}
              suggestions={{
                spreadsheet_id:
                  sheets.data?.spreadsheets.map((x) => ({
                    value: x.id,
                    label: x.name,
                  })) || [],
                range:
                  tabs.data?.sheets.map((x) => ({
                    value: `'${x.title.replaceAll("'", "''")}'!A:Z`,
                    label: x.title,
                  })) || [],
                query:
                  labels.data?.labels.map((x) => ({
                    value: `label:${x.name}`,
                    label: x.name,
                  })) || [],
              }}
              schema={type.config_schema}
              value={data.config}
              onChange={(config) => {
                setData({ ...data, config });
                setJobId('');
              }}
            />
            <small>
              Pour reprendre une donnée précédente :{' '}
              <code>{'{{ input.text }}'}</code>. Les champs JSON conservent leur
              type.
            </small>
          </>
        )}
        {step === 4 && (
          <>
            <p>
              Essayez votre agent avec une entrée JSON. Le test peut produire
              des effets réels si votre connexion est réelle.
            </p>
            <JsonEditor
              label="Entrée du test"
              onValidity={(valid) => {
                if (!valid) setInputValid(false);
              }}
              value={input}
              onChange={(v) => {
                const valid = !!v && typeof v === 'object' && !Array.isArray(v);
                setInputValid(valid);
                if (valid) setInput(v as Obj);
              }}
            />
            <button
              className="button secondary"
              type="button"
              disabled={
                busy ||
                !inputValid ||
                (job.isFetching && job.data?.status === 'running')
              }
              onClick={() => void test()}
            >
              {busy ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Play size={16} />
              )}
              Exécuter le test
            </button>
            {job.error && <ErrorBox error={job.error} />}
            {job.data && (
              <div className="test-result">
                <div className="section-heading">
                  <Badge status={job.data.status} />
                  <Link to={`/executions/${job.data.id}`} target="_blank">
                    Voir les détails
                  </Link>
                </div>
                {job.data.steps.map((s) => (
                  <div key={s.id}>
                    <strong>{s.node_id}</strong>
                    {s.error && <p className="field-error">{s.error}</p>}
                    <JsonView value={s.output || { status: s.status }} />
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        {step === 5 && (
          <>
            <div className="summary-card">
              <TypeIcon type={data.type_key || ''} />
              <div>
                <h3>{data.name || 'Agent sans nom'}</h3>
                <p>{type?.label}</p>
                <small>
                  {connections.data?.find((c) => c.id === data.connection_id)
                    ?.name || 'Sans connexion externe'}
                </small>
              </div>
            </div>
            <JsonView value={data.config} />
            <label className="check-field">
              <input
                type="checkbox"
                checked={activate}
                onChange={(e) => setActivate(e.target.checked)}
              />
              Activer cet agent après enregistrement
            </label>
            {job.data && (
              <p>
                Dernier test : <Badge status={job.data.status} />
              </p>
            )}
          </>
        )}
        <footer className="wizard-footer">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={async () => {
              if (step > 1) {
                setStep(step - 1);
                setError(null);
              } else {
                try {
                  await persist(data, step);
                  toast('Brouillon enregistré.');
                  navigate('/agents');
                } catch (e) {
                  setError(e);
                }
              }
            }}
          >
            {step > 1 ? 'Précédent' : 'Enregistrer et quitter'}
          </button>
          <button className="button primary" disabled={busy || !loaded}>
            {busy ? <LoaderCircle className="spin" size={17} /> : null}
            {step === 5
              ? id
                ? 'Enregistrer les modifications'
                : 'Créer l’agent'
              : 'Continuer'}
            <ArrowRight size={16} />
          </button>
        </footer>
      </form>
    </>
  );
}
