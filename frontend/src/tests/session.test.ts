import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renew } from '../api/client';
import { useSession } from '../stores/session';
beforeEach(() => useSession.getState().clear());
afterEach(() => {
  vi.unstubAllGlobals();
  useSession.getState().clear();
});
it('mutualise le renouvellement pour éviter la réutilisation concurrente du refresh token', async () => {
  useSession
    .getState()
    .accept({ access_token: 'old-access', refresh_token: 'old-refresh' });
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        access_token: 'new-access',
        refresh_token: 'new-refresh',
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal('fetch', fetch);
  const a = renew(),
    b = renew();
  expect(a).toBe(b);
  await expect(a).resolves.toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(useSession.getState().access).toBe('new-access');
  expect(sessionStorage.getItem('atelier.refresh')).toBe('new-refresh');
});
it('ne rétablit pas une session déconnectée pendant un renouvellement en cours', async () => {
  useSession
    .getState()
    .accept({ access_token: 'old-access', refresh_token: 'old-refresh' });
  let answer!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    ),
  );
  const pending = renew();
  useSession.getState().clear();
  answer(
    new Response(
      JSON.stringify({
        access_token: 'late-access',
        refresh_token: 'late-refresh',
      }),
      { status: 200 },
    ),
  );
  await expect(pending).resolves.toBe(false);
  expect(useSession.getState().authenticated).toBe(false);
  expect(sessionStorage.getItem('atelier.refresh')).toBeNull();
});
