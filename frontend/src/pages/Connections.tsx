import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  ExternalLink,
  ShieldCheck,
  FlaskConical,
  RefreshCw,
  Trash2,
  Check,
} from 'lucide-react';
import { api, post, errorMessage, ApiError } from '../api/client';
import { useAll, useResource } from '../api/queries';
import type { Connection } from '../api/types';
import { useToasts } from '../stores/session';
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageTitle,
  TypeIcon,
} from '../components/ui';
type Connector = {
  key: string;
  credential_fields: string[];
  required_credentials: string[];
  always_required_credentials: string[];
};
export function compatible(type: string, connection: Connection) {
  return type.startsWith('telegram')
    ? connection.type === 'telegram'
    : type.startsWith('sheets')
      ? connection.type === 'google'
      : type.startsWith('gmail')
        ? ['google', 'gmail_smtp'].includes(connection.type)
        : type === 'http_request'
          ? connection.type === 'http'
          : false;
}
export function ConnectionCreator({
  onCreated,
}: {
  onCreated?: (id?: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [type, setType] = useState('google'),
    [name, setName] = useState(''),
    [credentials, setCredentials] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const connectors = useResource<Connector[]>('/integrations/connectors');
  const status = useResource<{ mock_integrations: boolean }>(
    '/integrations/status',
  );
  const cache = useQueryClient();
  const toast = useToasts((s) => s.push);
  async function connectGoogle(connectionId?: string) {
    const popup = window.open(
      'about:blank',
      'atelier-google',
      'width=620,height=780',
    );
    setBusy(true);
    setError(null);
    try {
      const result = await api<{
        mock: boolean;
        authorization_url: string | null;
        connection?: Connection;
      }>(
        `/integrations/google/oauth/start${connectionId ? `?connection_id=${connectionId}` : ''}`,
      );
      if (result.authorization_url) {
        if (popup) popup.location.href = result.authorization_url;
        else window.location.assign(result.authorization_url);
        toast(
          'Terminez le consentement Google dans la fenêtre ouverte. La liste se met à jour automatiquement.',
        );
      } else {
        popup?.close();
        toast('Connexion Google de démonstration créée.');
      }
      await cache.invalidateQueries({ queryKey: ['/connections'] });
      onCreated?.(result.connection?.id);
      setOpen(false);
    } catch (e) {
      popup?.close();
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function create() {
    setBusy(true);
    setError(null);
    try {
      const c =
        type === 'telegram'
          ? await post<Connection>('/integrations/telegram/connect', {
              name: name || 'Bot Telegram',
              ...(credentials.token ? { token: credentials.token } : {}),
            })
          : await post<Connection>('/connections', {
              type,
              name: name || type,
              credentials,
            });
      await cache.invalidateQueries({ queryKey: ['/connections'] });
      onCreated?.(c.id);
      setOpen(false);
      setCredentials({});
      toast('Connexion enregistrée.');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        type="button"
        className="button secondary"
        onClick={() => setOpen(true)}
      >
        <Plus size={16} />
        Nouvelle connexion
      </button>
      {open && (
        <Modal
          title="Connecter un outil"
          onClose={() => {
            if (!busy) {
              setOpen(false);
              setCredentials({});
            }
          }}
        >
          <p>
            Les identifiants sont chiffrés et ne sont jamais affichés après leur
            enregistrement.
          </p>
          <label className="field">
            <span>Service</span>
            <select
              aria-label="Service"
              value={type}
              onChange={(e) => {
                setType(e.target.value);
                setCredentials({});
                setError(null);
              }}
            >
              {(
                connectors.data || [{ key: 'google' }, { key: 'telegram' }]
              ).map((c) => (
                <option key={c.key} value={c.key}>
                  {c.key === 'google'
                    ? 'Google · Gmail et Sheets'
                    : c.key === 'telegram'
                      ? 'Telegram'
                      : c.key === 'gmail_smtp'
                        ? 'Gmail · SMTP / IMAP'
                        : c.key}
                </option>
              ))}
            </select>
          </label>
          {type === 'google' ? (
            <>
              <p>Autorisez Gmail et Google Sheets avec votre compte Google.</p>
              <button
                className="button primary"
                disabled={busy}
                onClick={() => void connectGoogle()}
              >
                <ExternalLink size={17} />
                {status.data?.mock_integrations
                  ? 'Créer une connexion de démonstration'
                  : 'Continuer avec Google'}
              </button>
              <a
                className="text-link"
                href="/api/v1/integrations/google/checklist"
                target="_blank"
                rel="noreferrer"
              >
                Checklist de configuration Google <ExternalLink size={14} />
              </a>
            </>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                e.stopPropagation();
                void create();
              }}
            >
              <label className="field">
                <span>Nom de la connexion</span>
                <input
                  maxLength={120}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Mon outil"
                />
              </label>
              {(
                connectors.data?.find((c) => c.key === type)
                  ?.credential_fields || ['token']
              ).map((field) => (
                <label className="field" key={field}>
                  <span>
                    {field}
                    {type === 'telegram' && field === 'token'
                      ? ' (facultatif si configuré sur le serveur)'
                      : ''}
                  </span>
                  <input
                    autoComplete="off"
                    type={
                      ['base_url', 'email'].includes(field)
                        ? 'text'
                        : 'password'
                    }
                    value={credentials[field] || ''}
                    onChange={(e) =>
                      setCredentials({
                        ...credentials,
                        [field]: e.target.value,
                      })
                    }
                  />
                </label>
              ))}
              <button className="button primary" disabled={busy}>
                Enregistrer la connexion
              </button>
            </form>
          )}
          {error != null && (
            <>
              <ErrorBox error={error} />
              {error instanceof ApiError &&
                [
                  'redirect_uri_mismatch',
                  'access_denied',
                  'oauth_error',
                ].includes(error.code) && (
                  <a
                    href="/api/v1/integrations/google/checklist"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Consulter la checklist Google
                  </a>
                )}
            </>
          )}
        </Modal>
      )}
    </>
  );
}
export function Connections() {
  const connections = useAll<Connection>('/connections', 5000);
  const status = useResource<{
    mock_integrations: boolean;
    warning: string | null;
  }>('/integrations/status');
  const [busy, setBusy] = useState(''),
    [deleting, setDeleting] = useState<Connection | null>(null);
  const cache = useQueryClient(),
    toast = useToasts((s) => s.push);
  async function test(c: Connection) {
    setBusy(c.id);
    try {
      const result = await post<{ ok?: boolean; detail?: string }>(
        `/connections/${c.id}/test`,
      );
      if (result.ok === false)
        throw new Error(result.detail || 'Le test a échoué.');
      toast('Connexion testée avec succès.');
      await cache.invalidateQueries({ queryKey: ['/connections'] });
    } catch (e) {
      toast(errorMessage(e), 'error');
    } finally {
      setBusy('');
    }
  }
  async function reconnect(c: Connection) {
    const popup = window.open(
      'about:blank',
      'atelier-google',
      'width=620,height=780',
    );
    try {
      const data = await api<{ authorization_url: string | null }>(
        `/integrations/google/oauth/start?connection_id=${c.id}`,
      );
      if (data.authorization_url) {
        if (popup) popup.location.href = data.authorization_url;
        else window.location.assign(data.authorization_url);
      } else {
        popup?.close();
        void cache.invalidateQueries({ queryKey: ['/connections'] });
      }
    } catch (e) {
      popup?.close();
      toast(errorMessage(e), 'error');
    }
  }
  return (
    <>
      <PageTitle
        title="Vos connexions"
        description="Tous vos outils, reliés à votre atelier."
        actions={<ConnectionCreator />}
      />
      <div
        className={`connection-notice ${status.data?.mock_integrations ? 'demo' : ''}`}
      >
        {status.data?.mock_integrations ? (
          <FlaskConical size={23} />
        ) : (
          <ShieldCheck size={23} />
        )}
        <div>
          <strong>
            {status.data?.mock_integrations
              ? 'Mode démonstration'
              : 'Vos identifiants restent privés'}
          </strong>
          <p>
            {status.data?.mock_integrations
              ? 'Les actions sont simulées. Aucun email ou message externe n’est envoyé.'
              : 'Les secrets sont chiffrés au repos et utilisés uniquement par vos agents.'}
          </p>
        </div>
      </div>
      {status.error && <ErrorBox error={status.error} />}
      <section className="panel">
        {connections.isLoading ? (
          <Loading />
        ) : connections.error ? (
          <ErrorBox
            error={connections.error}
            retry={() => void connections.refetch()}
          />
        ) : !connections.data?.length ? (
          <Empty
            title="Connectez votre premier outil"
            text="Une même connexion Google sert à Gmail et Google Sheets."
            action={<ConnectionCreator />}
          />
        ) : (
          <div className="connection-list">
            {connections.data.map((c) => (
              <div className="connection-row" key={c.id}>
                <TypeIcon type={c.type} />
                <div className="connection-info">
                  <strong>{c.name}</strong>
                  <small>
                    {c.type === 'google' ? 'Gmail + Google Sheets' : c.type}{' '}
                    {c.is_mock ? '· Démonstration' : '· Réelle'}
                  </small>
                </div>
                <Badge status={c.status} />
                <button
                  className="button secondary"
                  disabled={busy === c.id}
                  onClick={() => void test(c)}
                >
                  <Check size={15} />
                  Tester
                </button>
                {c.type === 'google' && (
                  <button
                    aria-label={`Reconnecter ${c.name}`}
                    className="icon-button"
                    onClick={() => void reconnect(c)}
                  >
                    <RefreshCw size={16} />
                  </button>
                )}
                <button
                  className="icon-button"
                  aria-label={`Révoquer ${c.name}`}
                  onClick={() => setDeleting(c)}
                >
                  <Trash2 size={17} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
      <div className="help-card">
        <h3>Un problème avec Google ?</h3>
        <p>
          Vérifiez l’utilisateur de test, les API activées et l’URI de
          redirection dans Google Cloud. Une erreur{' '}
          <code>redirect_uri_mismatch</code> indique une URI différente ;{' '}
          <code>access_denied</code> peut indiquer une autorisation refusée ou
          un utilisateur absent de l’audience de test.
        </p>
        <a
          className="text-link"
          target="_blank"
          rel="noreferrer"
          href="/api/v1/integrations/google/checklist"
        >
          Ouvrir la checklist Google <ExternalLink size={15} />
        </a>
      </div>
      {deleting && (
        <Modal
          title="Révoquer la connexion ?"
          onClose={() => setDeleting(null)}
        >
          <p>
            Les agents utilisant « {deleting.name} » devront être reconnectés.
            Les jetons Google réels seront révoqués.
          </p>
          <button
            className="button danger"
            disabled={!!busy}
            onClick={async () => {
              setBusy(deleting.id);
              try {
                await api(`/connections/${deleting.id}`, { method: 'DELETE' });
                setDeleting(null);
                await cache.invalidateQueries({ queryKey: ['/connections'] });
                toast('Connexion révoquée.');
              } catch (e) {
                toast(errorMessage(e), 'error');
              } finally {
                setBusy('');
              }
            }}
          >
            Révoquer
          </button>
        </Modal>
      )}
    </>
  );
}
