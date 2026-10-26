import { test, expect } from '@playwright/test';

for (const status of ['failed', 'success']) {
  test(`affiche un ancien test d’agent ${status} sans géométrie de canevas`, async ({
    page,
  }) => {
    const id = '00000000-0000-4000-8000-000000000001';
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(() =>
      sessionStorage.setItem('atelier.refresh', 'history-test'),
    );
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/v1/auth/refresh')
        return route.fulfill({
          json: {
            access_token: 'history-access',
            refresh_token: 'history-test',
          },
        });
      if (path === '/api/v1/auth/me')
        return route.fulfill({ json: { email: 'history@example.com' } });
      if (path === '/api/v1/integrations/status')
        return route.fulfill({ json: { mock_integrations: true } });
      if (path.endsWith('/stream'))
        return route.fulfill({
          contentType: 'text/event-stream',
          body: 'event: complete\ndata: {}\n\n',
        });
      if (path === `/api/v1/executions/${id}`)
        return route.fulfill({
          json: {
            id,
            workflow_id: null,
            workflow_version: null,
            workflow_name: 'Test d’agent archivé',
            status,
            source: 'agent_test',
            trigger_payload: { text: 'Facture synthétique' },
            cancel_requested: false,
            error: status === 'failed' ? 'Chemin JSON introuvable' : null,
            error_code: status === 'failed' ? 'invalid_mapping' : null,
            created_at: '2026-10-26T12:00:00Z',
            started_at: '2026-10-26T12:00:00Z',
            finished_at: '2026-10-26T12:00:01Z',
            graph: {
              nodes: [
                {
                  id: 'test',
                  data: {
                    agent_id: 'test',
                    input: {},
                    timeout_seconds: 60,
                    retries: 0,
                  },
                },
              ],
              edges: [],
            },
            steps: [
              {
                id: 'step',
                node_id: 'test',
                status,
                input: { text: 'Facture synthétique' },
                output: status === 'success' ? { label: 'facture' } : null,
                attempt: 1,
                error: status === 'failed' ? 'Chemin JSON introuvable' : null,
                error_code: status === 'failed' ? 'invalid_mapping' : null,
                started_at: '2026-10-26T12:00:00Z',
                finished_at: '2026-10-26T12:00:01Z',
              },
            ],
          },
        });
      return route.fulfill({
        status: 404,
        json: { detail: 'Route de test absente' },
      });
    });
    await page.goto(`/executions/${id}`);
    await expect(
      page.getByRole('heading', { name: 'Test d’agent archivé' }),
    ).toBeVisible();
    await expect(page.locator(`.flow-agent.node-${status}`)).toBeVisible();
    await expect(page.locator('.step-detail')).toHaveCount(1);
    await expect(page.locator('.step-content')).toContainText(
      'Facture synthétique',
    );
    await expect(
      page.getByText('L’interface a rencontré un problème.'),
    ).toHaveCount(0);
    if (status === 'failed')
      await expect(page.locator('.step-content')).toContainText(
        'invalid_mapping',
      );
    else await expect(page.locator('.step-content')).toContainText('facture');
    expect(errors).toEqual([]);
  });
}
