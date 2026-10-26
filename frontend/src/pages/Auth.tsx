import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowRight, GitBranch, Check, LoaderCircle } from 'lucide-react';
import { post } from '../api/client';
import { useSession, type Tokens } from '../stores/session';
import { ErrorBox } from '../components/ui';
export function Auth({ register = false }: { register?: boolean }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const navigate = useNavigate();
  const cache = useQueryClient();
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (register) await post('/auth/register', { email, password });
      const tokens = await post<Tokens>('/auth/login', { email, password });
      cache.clear();
      useSession.getState().accept(tokens);
      navigate('/', { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-screen">
      <aside className="auth-story">
        <Link className="brand" to="/">
          <span className="brand-mark">
            <GitBranch />
          </span>
          atelier<span className="brand-dot">.</span>
        </Link>
        <div>
          <span className="eyebrow">VOTRE ATELIER D’AUTOMATISATION</span>
          <h1>
            Des idées aux actions.
            <br />
            <em>Un agent à la fois.</em>
          </h1>
          <p>
            Connectez vos outils, assemblez vos agents et laissez vos workflows
            prendre le relais.
          </p>
          <div className="auth-flow">
            <span>Telegram</span>
            <ArrowRight />
            <span>Classification</span>
            <ArrowRight />
            <span>Sheets</span>
          </div>
        </div>
        <small>
          <Check size={16} /> Vos connexions sont chiffrées et restent privées.
        </small>
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          <span className="eyebrow">BIENVENUE DANS ATELIER</span>
          <h2>
            {register ? 'Créez votre espace' : 'Heureux de vous retrouver'}
          </h2>
          <p>
            {register
              ? 'Tout commence par votre premier agent.'
              : 'Connectez-vous pour retrouver vos automatisations.'}
          </p>
          <form onSubmit={submit}>
            {error != null && <ErrorBox error={error} />}
            <label className="field">
              <span>Adresse email</span>
              <input
                autoComplete="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="vous@exemple.com"
              />
            </label>
            <label className="field">
              <span>Mot de passe</span>
              <input
                type="password"
                autoComplete={register ? 'new-password' : 'current-password'}
                required
                minLength={register ? 12 : 1}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {register && <small>Au moins 12 caractères.</small>}
            </label>
            <button className="button primary full" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={18} /> : null}
              {register ? 'Créer mon compte' : 'Se connecter'}
              <ArrowRight size={17} />
            </button>
          </form>
          <p className="auth-switch">
            {register ? 'Déjà un compte ?' : 'Pas encore de compte ?'}{' '}
            <Link to={register ? '/login' : '/register'}>
              {register ? 'Se connecter' : 'Créer un compte'}
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
