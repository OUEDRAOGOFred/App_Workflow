import { refreshToken, useSession, type Tokens } from '../stores/session';
export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = '',
    public helpUrl?: string,
  ) {
    super(message);
  }
}
export async function readResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const data = await response
    .json()
    .catch(() => ({ detail: 'Réponse du serveur illisible.' }));
  if (!response.ok) {
    const detail = data.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail
              .map(
                (x: { msg: string; loc: string[] }) =>
                  `${x.loc.slice(1).join('.')}: ${x.msg}`,
              )
              .join(' · ')
          : detail?.detail || data.message || 'La requête a échoué.';
    throw new ApiError(
      msg,
      response.status,
      detail?.code || data.code,
      detail?.help_url || data.help_url,
    );
  }
  return data as T;
}
let renewing: Promise<boolean> | null = null;
export function renew() {
  if (!renewing) {
    renewing = (async () => {
      const token = refreshToken();
      if (!token) return false;
      try {
        const r = await fetch('/api/v1/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: token }),
        });
        const data = await readResponse<Tokens>(r);
        if (refreshToken() !== token) return false;
        useSession.getState().accept(data);
        return true;
      } catch {
        useSession.getState().clear();
        return false;
      }
    })().finally(() => {
      renewing = null;
    });
  }
  return renewing;
}
export async function initialize() {
  await renew();
  useSession.getState().initialized();
}
export async function api<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    signal?: AbortSignal;
    headers?: Record<string, string>;
  } = {},
  retry = true,
): Promise<T> {
  const token = useSession.getState().access;
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method: options.method || 'GET',
      credentials: 'include',
      signal: options.signal,
      headers: {
        ...(options.body !== undefined
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(
      'Serveur inaccessible. Vérifiez que les services sont démarrés.',
    );
  }
  if (response.status === 401 && retry && !path.startsWith('/auth/')) {
    if (await renew()) return api<T>(path, options, false);
    useSession.getState().clear();
  }
  return readResponse<T>(response);
}
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body });
export const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : 'Une erreur est survenue.';
