import { expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { JsonEditor } from '../components/ui';
import { validateConfig } from '../components/SchemaForm';
it('refuse une liste à la place d’une entrée objet sans réutiliser silencieusement l’ancienne entrée', () => {
  const changed = vi.fn(),
    valid = vi.fn();
  render(
    <JsonEditor
      value={{ text: 'avant' }}
      onChange={changed}
      onValidity={valid}
    />,
  );
  fireEvent.change(screen.getByLabelText('Objet JSON'), {
    target: { value: '[]' },
  });
  expect(changed).not.toHaveBeenCalled();
  expect(valid).toHaveBeenLastCalledWith(false);
  expect(screen.getByLabelText('Objet JSON')).toBeInvalid();
  fireEvent.change(screen.getByLabelText('Objet JSON'), {
    target: { value: '{"text":"après"}' },
  });
  expect(changed).toHaveBeenLastCalledWith({ text: 'après' });
  expect(screen.getByLabelText('Objet JSON')).toBeValid();
});
it('valide les contraintes utiles des configurations du catalogue sans génération de code', () => {
  const schema = {
    type: 'object',
    required: ['classes', 'threshold'],
    additionalProperties: false,
    properties: {
      classes: {
        type: 'array',
        uniqueItems: true,
        minItems: 1,
        items: { type: 'string', minLength: 1 },
      },
      threshold: { type: 'number', minimum: 0, maximum: 1 },
    },
  };
  expect(
    validateConfig(schema, { classes: ['facture', 'cv'], threshold: 0.5 }),
  ).toEqual([]);
  expect(
    validateConfig(schema, {
      classes: ['cv', 'cv'],
      threshold: 2,
      unexpected: true,
    }),
  ).toHaveLength(3);
  expect(validateConfig(schema, { classes: [] })).toHaveLength(2);
});
