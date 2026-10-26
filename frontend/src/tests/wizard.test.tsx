import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AgentWizard } from '../pages/AgentWizard';
import { api, post } from '../api/client';
vi.mock('../api/client', () => ({
  api: vi.fn(),
  post: vi.fn(),
  errorMessage: (e: Error) => e.message,
}));
const types = [
  {
    key: 'transform',
    label: 'Transformation',
    description: 'Construire un objet',
    requires_connection: false,
    icon: 'braces',
    config_schema: {
      type: 'object',
      properties: { mapping: { type: 'object' } },
      required: ['mapping'],
    },
  },
  {
    key: 'telegram_send',
    label: 'Réponse Telegram',
    description: 'Envoyer un message',
    requires_connection: true,
    icon: 'telegram',
    config_schema: {
      type: 'object',
      properties: { text: { type: 'string' } },
      required: ['text'],
    },
  },
];
function mount(path = '/agents/new') {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/agents/new" element={<AgentWizard />} />
          <Route path="/agents" element={<h1>Liste des agents</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/agent-types') return types;
    if (path.startsWith('/connections')) return [];
    if (path.startsWith('/agent-drafts/'))
      return {
        id: 'draft1',
        step: 3,
        data: {
          type_key: 'transform',
          name: 'Agent repris',
          description: '',
          connection_id: null,
          config: { mapping: { text: 'bonjour' } },
        },
      };
    return {};
  });
  vi.mocked(post).mockImplementation(async (path, body) => {
    if (path === '/agent-drafts') return { id: 'draft1', ...(body as object) };
    if (path === '/agents') return { id: 'agent1', ...(body as object) };
    return {};
  });
});
describe('assistant de création', () => {
  it('présente cinq étapes et empêche de poursuivre sans type', async () => {
    mount();
    expect(
      await screen.findByText('Quel rôle voulez-vous confier à votre agent ?'),
    ).toBeVisible();
    expect(screen.getAllByText(/^ÉTAPE \d$/)).toHaveLength(5);
    await userEvent.click(screen.getByRole('button', { name: /Continuer/ }));
    expect(screen.getByRole('alert')).toHaveTextContent('Choisissez un type');
  });
  it('exige une connexion pour un agent Telegram', async () => {
    mount();
    await userEvent.click(
      await screen.findByRole('button', { name: /Réponse Telegram/ }),
    );
    await userEvent.click(screen.getByRole('button', { name: /Continuer/ }));
    await userEvent.click(screen.getByRole('button', { name: /Continuer/ }));
    expect(screen.getByRole('combobox', { name: 'Connexion' })).toBeInvalid();
    expect(screen.queryByLabelText('Nom de l’agent *')).not.toBeInTheDocument();
  });
  it('crée et active un agent à partir d’un mapping valide', async () => {
    mount();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole('button', { name: /Transformation/ }),
    );
    await user.click(screen.getByRole('button', { name: /Continuer/ }));
    expect(screen.getByText('Aucune connexion nécessaire')).toBeVisible();
    await user.click(screen.getByRole('button', { name: /Continuer/ }));
    await user.type(screen.getByLabelText('Nom de l’agent *'), 'Mon agent');
    fireEvent.change(screen.getByLabelText('Mapping de sortie *'), {
      target: { value: '{"text":"{{ input.text }}"}' },
    });
    await user.click(screen.getByRole('button', { name: /Continuer/ }));
    expect(screen.getByLabelText('Entrée du test')).toBeVisible();
    await user.click(screen.getByRole('button', { name: /Continuer/ }));
    await user.click(screen.getByRole('button', { name: /Créer l’agent/ }));
    await screen.findByText('Liste des agents');
    expect(post).toHaveBeenCalledWith(
      '/agents',
      expect.objectContaining({
        name: 'Mon agent',
        config: { mapping: { text: '{{ input.text }}' } },
      }),
    );
    expect(post).toHaveBeenCalledWith('/agents/agent1/activate');
  });
  it('reprend un brouillon enregistré côté serveur', async () => {
    mount('/agents/new?draft=draft1');
    await waitFor(() =>
      expect(screen.getByLabelText('Nom de l’agent *')).toHaveValue(
        'Agent repris',
      ),
    );
    expect(
      (screen.getByLabelText('Mapping de sortie *') as HTMLTextAreaElement)
        .value,
    ).toContain('bonjour');
  });
  it('bloque un mapping JSON invalide', async () => {
    mount('/agents/new?draft=draft1');
    const mapping = await screen.findByLabelText('Mapping de sortie *');
    fireEvent.change(mapping, { target: { value: '{broken' } });
    await userEvent.click(screen.getByRole('button', { name: /Continuer/ }));
    expect(mapping).toBeInvalid();
    expect(screen.queryByLabelText('Entrée du test')).not.toBeInTheDocument();
  });
});
it('réutilise l’agent créé si seule son activation a échoué', async () => {
  const base = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (path, options, retry) => {
    if (path === '/agent-drafts/draft1')
      return {
        id: 'draft1',
        step: 5,
        data: {
          type_key: 'transform',
          name: 'Agent à activer',
          description: '',
          connection_id: null,
          config: { mapping: { ok: true } },
        },
      };
    if (path === '/agents/agent1') return { id: 'agent1' };
    return base(path, options, retry);
  });
  let activation = 0;
  const basePost = vi.mocked(post).getMockImplementation()!;
  vi.mocked(post).mockImplementation(async (path, body) => {
    if (path === '/agents/agent1/activate' && activation++ === 0)
      throw new Error('Activation temporairement indisponible');
    return basePost(path, body);
  });
  mount('/agents/new?draft=draft1');
  const submit = await screen.findByRole('button', { name: /Créer l’agent/ });
  await userEvent.click(submit);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Activation temporairement indisponible',
  );
  await userEvent.click(submit);
  await screen.findByText('Liste des agents');
  expect(
    vi.mocked(post).mock.calls.filter(([path]) => path === '/agents'),
  ).toHaveLength(1);
  expect(api).toHaveBeenCalledWith(
    '/agents/agent1',
    expect.objectContaining({ method: 'PUT' }),
  );
});
