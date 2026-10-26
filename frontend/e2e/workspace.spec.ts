import { test, expect, type Page } from '@playwright/test';
test('cinq agents créés dans le wizard, graphe visuel, exécution et session', async ({
  page,
  context,
  baseURL,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const stamp = Date.now();
  const email = `atelier-${stamp}@example.com`;
  const password = 'Atelier-test-password-2026!';
  await page.goto('/register');
  await page.getByLabel('Adresse email').fill(email);
  await page.getByLabel('Mot de passe').fill(password);
  const login = page.waitForResponse(
    (r) =>
      r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Créer mon compte' }).click();
  const tokens = await (await login).json();
  await expect(
    page.getByRole('heading', { name: 'Votre atelier, en mouvement' }),
  ).toBeVisible();
  await page.screenshot({
    path: 'test-results/dashboard-desktop.png',
    fullPage: true,
  });
  const headers = { Authorization: `Bearer ${tokens.access_token}` };
  const status = await context.request.get(
    `${baseURL}/api/v1/integrations/status`,
    { headers },
  );
  expect(
    (await status.json()).mock_integrations,
    'Le test nécessite le mode démonstration',
  ).toBe(true);
  async function next() {
    await page.getByRole('button', { name: 'Continuer', exact: true }).click();
  }
  async function createAgent(
    type: string,
    name: string,
    connection?: string,
    configure?: (p: Page) => Promise<void>,
  ) {
    await page.goto('/agents/new');
    if (name.startsWith('01'))
      await page.screenshot({
        path: 'test-results/wizard-types.png',
        fullPage: true,
      });
    await page.getByRole('button', { name: new RegExp(type) }).click();
    await next();
    if (connection)
      await page
        .getByRole('combobox', { name: 'Connexion', exact: true })
        .selectOption({ label: connection });
    await next();
    await page.getByLabel('Nom de l’agent *', { exact: true }).fill(name);
    if (configure) await configure(page);
    if (name.startsWith('03'))
      await page.screenshot({
        path: 'test-results/wizard-configuration.png',
        fullPage: true,
      });
    await next();
    await expect(
      page.getByLabel('Entrée du test', { exact: true }),
    ).toBeVisible();
    if (name.startsWith('02')) {
      await page
        .getByRole('button', { name: 'Exécuter le test', exact: true })
        .click();
      await expect(
        page.locator('.test-result .section-heading .badge'),
      ).toHaveText('Réussie');
    }
    await next();
    await page
      .getByRole('button', { name: 'Créer l’agent', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Vos agents', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { name, exact: true }),
    ).toBeVisible();
  }
  let agents: { id: string; name: string; type_key: string }[] = [];
  let workflows: { id: string }[] = [];
  let connections: { id: string }[] = [];
  try {
    await page.getByRole('link', { name: 'Connexions', exact: true }).click();
    await page
      .getByRole('button', { name: 'Nouvelle connexion' })
      .first()
      .click();
    await page
      .getByRole('button', { name: 'Créer une connexion de démonstration' })
      .click();
    await expect(
      page.getByText('Google de démonstration', { exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Nouvelle connexion' })
      .first()
      .click();
    await page
      .getByRole('combobox', { name: 'Service', exact: true })
      .selectOption('telegram');
    await page.getByLabel('Nom de la connexion').fill('Telegram atelier test');
    await page
      .getByRole('button', { name: 'Enregistrer la connexion' })
      .click();
    await expect(
      page.getByText('Telegram atelier test', { exact: true }),
    ).toBeVisible();
    await createAgent(
      'Réception Telegram',
      '01 · Réception',
      'Telegram atelier test · démo',
    );
    await createAgent(
      'Classification de document',
      '02 · Classification',
      undefined,
      async (p) => {
        await p.getByLabel('Seuil de confiance').fill('0');
      },
    );
    await createAgent(
      'Ajouter une ligne Sheets',
      '03 · Archivage',
      'Google de démonstration · démo',
      async (p) => {
        await p.getByLabel('Classeur Google Sheets *').fill('demo-sheet');
        await p.getByLabel('Feuille et plage *').fill('Documents!A:B');
        await p
          .getByLabel('Valeurs de la ligne *')
          .fill('["{{ input.label }}", "{{ input.confidence }}"]');
      },
    );
    await createAgent(
      'Envoi Gmail',
      '04 · Email',
      'Google de démonstration · démo',
      async (p) => {
        await p.getByLabel('Destinataire *').fill('recipient@example.com');
        await p.getByLabel('Objet *').fill('Document classé');
        await p
          .getByLabel('Contenu *', { exact: true })
          .fill('Classe : {{ input.label }}');
      },
    );
    await createAgent(
      'Réponse Telegram',
      '05 · Réponse',
      'Telegram atelier test · démo',
      async (p) => {
        await p.getByLabel('Identifiant du chat *').fill('demo-chat');
        await p
          .getByLabel('Message *', { exact: true })
          .fill('Document classé.');
      },
    );
    await page.reload();
    await expect(
      page.getByRole('heading', { name: 'Vos agents', exact: true }),
    ).toBeVisible();
    expect(await page.locator('.agent-card').count()).toBe(5);
    await page.screenshot({
      path: 'test-results/agents-desktop.png',
      fullPage: true,
    });
    await page.getByRole('button', { name: 'Mode sombre' }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.getByRole('button', { name: 'Mode clair' }).click();
    await page.getByRole('link', { name: /^Workflows/ }).click();
    await page
      .getByRole('link', { name: 'Nouveau workflow', exact: true })
      .click();
    await page
      .getByLabel('Nom du workflow', { exact: true })
      .fill('Documents · Démonstration');
    for (const name of [
      '01 · Réception',
      '02 · Classification',
      '03 · Archivage',
      '04 · Email',
      '05 · Réponse',
    ]) {
      const agent = page
        .locator('.palette')
        .getByRole('button', { name: new RegExp(name) });
      if (name.startsWith('01'))
        await agent.dragTo(page.locator('.react-flow__pane'), {
          targetPosition: { x: 100, y: 180 },
        });
      else await agent.click();
    }
    const nodes = page.locator('.react-flow__node');
    await expect(nodes).toHaveCount(5);
    // The controls fit the actual node positions before linking the handles.
    await page.getByRole('button', { name: 'fit view' }).click();
    const classifierNode = await nodes.nth(1).getAttribute('data-id');
    for (const i of [3, 4]) {
      await nodes.nth(i).click();
      await page
        .getByLabel('Mapping d’entrée (objet JSON)', { exact: true })
        .fill(
          JSON.stringify({
            label: `{{ nodes.${classifierNode}.output.label }}`,
          }),
        );
    }

    for (let i = 0; i < 4; i++) {
      const source = await nodes.nth(i).locator('.source').boundingBox();
      const target = await nodes
        .nth(i + 1)
        .locator('.target')
        .boundingBox();
      expect(source).not.toBeNull();
      expect(target).not.toBeNull();
      await page.mouse.move(
        source!.x + source!.width / 2,
        source!.y + source!.height / 2,
      );
      await page.mouse.down();
      await page.mouse.move(
        target!.x + target!.width / 2,
        target!.y + target!.height / 2,
        { steps: 12 },
      );
      await page.mouse.up();
    }
    await expect(page.locator('.react-flow__edge')).toHaveCount(4);
    await page
      .locator('.react-flow__pane')
      .click({ position: { x: 25, y: 25 } });
    await page
      .getByRole('combobox', { name: 'Déclencheur', exact: true })
      .selectOption('telegram');
    await page
      .getByRole('combobox', { name: /Connexion du déclencheur/ })
      .selectOption({ label: 'Telegram atelier test' });
    await page
      .getByLabel('Entrée du lancement manuel')
      .fill(
        '{"text":"Facture fournisseur, montant total 150 euros.","chat_id":"demo-chat"}',
      );
    await page
      .getByRole('button', { name: 'Enregistrer', exact: true })
      .click();
    await expect(
      page.getByText('Workflow enregistré.', { exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await expect(
      page.getByText('Graphe valide : votre workflow est prêt.'),
    ).toBeVisible();
    await page.screenshot({
      path: 'test-results/workflow-desktop.png',
      fullPage: true,
    });
    await page.getByRole('button', { name: 'Activer', exact: true }).click();
    await expect(page.locator('.editor-name .badge')).toHaveText('Actif');
    await page.getByRole('button', { name: 'Exécuter', exact: true }).click();
    await expect(
      page.getByRole('heading', {
        name: 'Documents · Démonstration',
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator('.page-title .badge')).toHaveText('Réussie', {
      timeout: 60000,
    });
    await expect(page.locator('.step-detail')).toHaveCount(5);
    await expect(page.getByText('complete', { exact: true })).toBeVisible();
    await expect(page.locator('.flow-agent.node-success')).toHaveCount(5);
    const classification = page.locator('.step-detail').nth(1);
    await classification.locator('summary').click();
    expect(
      JSON.parse(await classification.locator('pre').last().innerText()).label,
    ).toBe('facture');
    await page.screenshot({
      path: 'test-results/execution-desktop.png',
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: 'Ouvrir le menu' }).click();
    await page
      .getByRole('link', { name: 'Vue d’ensemble', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Votre atelier, en mouvement' }),
    ).toBeVisible();
    await page.screenshot({
      path: 'test-results/dashboard-mobile.png',
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    expect(errors).toEqual([]);
  } finally {
    const get = async <T>(path: string): Promise<T[]> => {
      const result = await context.request.get(`${baseURL}/api/v1${path}`, {
        timeout: 10000,
        headers,
      });
      return result.ok() ? await result.json() : [];
    };
    agents = await get('/agents');
    workflows = await get('/workflows');
    connections = await get('/connections');
    const drafts = await get<{ id: string }>('/agent-drafts');
    for (const [kind, items] of [
      ['workflows', workflows],
      ['agents', agents],
      ['agent-drafts', drafts],
      ['connections', connections],
    ] as const)
      for (const item of items)
        await context.request.delete(`${baseURL}/api/v1/${kind}/${item.id}`, {
          headers,
        });
  }
});
