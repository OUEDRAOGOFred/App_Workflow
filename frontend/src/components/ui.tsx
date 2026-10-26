import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  Check,
  LoaderCircle,
  X,
  Bot,
  Send,
  Mail,
  Table2,
  BrainCircuit,
  GitBranch,
  Globe,
  Braces,
  Zap,
} from 'lucide-react';
import { useToasts } from '../stores/session';
export const date = (v: string | null | undefined) =>
  v
    ? new Date(v).toLocaleString('fr-FR', {
        dateStyle: 'short',
        timeStyle: 'short',
      })
    : 'Jamais';
const labels: Record<string, string> = {
  active: 'Actif',
  draft: 'Brouillon',
  error: 'En erreur',
  disabled: 'Inactif',
  expired: 'Expirée',
  queued: 'En attente',
  pending: 'En attente',
  running: 'En cours',
  success: 'Réussie',
  failed: 'Échec',
  cancelled: 'Annulée',
  skipped: 'Ignorée',
  revoked: 'Révoquée',
};
export function Badge({ status }: { status: string }) {
  return (
    <span className={`badge status-${status}`}>
      <i />
      {labels[status] || status}
    </span>
  );
}
export function TypeIcon({ type, size = 20 }: { type: string; size?: number }) {
  const Icon = type.startsWith('telegram')
    ? Send
    : type.startsWith('gmail')
      ? Mail
      : type.startsWith('sheets') || type === 'google'
        ? Table2
        : type === 'document_classifier' || type === 'llm'
          ? BrainCircuit
          : type === 'condition'
            ? GitBranch
            : type === 'http_request' || type === 'http'
              ? Globe
              : type === 'transform'
                ? Braces
                : Bot;
  return (
    <span className={`type-icon type-${type.split('_')[0]}`}>
      <Icon size={size} />
    </span>
  );
}
export function PageTitle({
  eyebrow = 'ESPACE DE TRAVAIL',
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="actions">{actions}</div>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status" aria-label="Chargement">
      <div className="loading-caption">
        <LoaderCircle className="spin" size={20} /> Chargement…
      </div>
      <div className="skeleton-grid" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div className="skeleton-card" key={i}>
            <i />
            <b />
            <span />
            <span />
          </div>
        ))}
      </div>
    </div>
  );
}
export function ErrorBox({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={19} />
      <div>
        {error instanceof Error ? error.message : String(error)}{' '}
        {retry && <button onClick={retry}>Réessayer</button>}
      </div>
    </div>
  );
}
export function Empty({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Zap size={26} />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}
export function JsonEditor({
  value,
  onChange,
  label = 'Objet JSON',
  onValidity,
  expected = 'object',
}: {
  value: unknown;
  onChange: (value: unknown) => void;
  label?: string;
  onValidity?: (valid: boolean) => void;
  expected?: 'object' | 'array' | 'any';
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState('');
  return (
    <label className="field">
      <span>{label}</span>
      <textarea
        aria-label={label}
        className="code-input"
        rows={6}
        value={text}
        aria-invalid={!!error}
        spellCheck={false}
        onChange={(e) => {
          const text = e.target.value;
          setText(text);
          try {
            const value = JSON.parse(text);
            if (
              expected === 'object' &&
              (value === null ||
                typeof value !== 'object' ||
                Array.isArray(value))
            )
              throw new Error('Un objet JSON est requis.');
            if (expected === 'array' && !Array.isArray(value))
              throw new Error('Une liste JSON est requise.');
            onChange(value);
            setError('');
            onValidity?.(true);
            e.target.setCustomValidity('');
          } catch (error) {
            const message =
              error instanceof SyntaxError
                ? 'JSON invalide'
                : error instanceof Error
                  ? error.message
                  : 'JSON invalide';
            setError(message);
            onValidity?.(false);
            e.target.setCustomValidity(message);
          }
        }}
      />
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const panel = ref.current;
    const items = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]',
        ) || [],
      );
    items()[0]?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
      }
      if (e.key === 'Tab') {
        const focusable = items();
        const first = focusable[0],
          last = focusable.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    panel?.addEventListener('keydown', key);
    return () => {
      panel?.removeEventListener('keydown', key);
      previous?.focus();
    };
  }, []);
  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={ref}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="section-heading">
          <h2>{title}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="Fermer"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>,
    document.body,
  );
}
export function Toasts() {
  const { items, remove } = useToasts();
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div className={`toast ${t.kind}`} key={t.id}>
          {t.kind === 'error' ? <AlertCircle size={19} /> : <Check size={19} />}
          <span>{t.text}</span>
          <button
            aria-label="Fermer la notification"
            onClick={() => remove(t.id)}
          >
            <X size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
