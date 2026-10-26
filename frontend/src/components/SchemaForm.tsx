import type { Json, Obj, Schema } from '../api/types';
import { JsonEditor } from './ui';
export function defaults(schema: Schema): Obj {
  return Object.fromEntries(
    Object.entries(schema.properties || {})
      .filter(([, v]) => v.default !== undefined)
      .map(([k, v]) => [k, v.default as Json]),
  );
}
// The form checks its supported JSON Schema fields without runtime code generation.
// The API validates the complete schema and remains authoritative for custom types.
export function validateConfig(schema: Schema, data: Obj): string[] {
  const errors: string[] = [];
  function check(s: Schema, v: Json, path: string) {
    const fail = (message: string) => errors.push(`${path} : ${message}`);
    if (s.enum && !s.enum.includes(String(v))) fail('valeur non autorisée');
    if (s.type === 'object') {
      if (v === null || typeof v !== 'object' || Array.isArray(v)) {
        fail('objet requis');
        return;
      }
      for (const key of s.required || [])
        if (!(key in v)) fail(`champ requis : ${key}`);
      if (
        s.maxProperties !== undefined &&
        Object.keys(v).length > s.maxProperties
      )
        fail('trop de champs');
      for (const [key, child] of Object.entries(v)) {
        if (s.properties?.[key])
          check(s.properties[key], child, `${path}.${key}`);
        else if (s.additionalProperties === false)
          fail(`champ inconnu : ${key}`);
      }
    } else if (s.type === 'array') {
      if (!Array.isArray(v)) {
        fail('liste requise');
        return;
      }
      if (s.minItems !== undefined && v.length < s.minItems)
        fail(`au moins ${s.minItems} élément(s)`);
      if (s.maxItems !== undefined && v.length > s.maxItems)
        fail(`au plus ${s.maxItems} élément(s)`);
      if (
        s.uniqueItems &&
        new Set(v.map((x) => JSON.stringify(x))).size !== v.length
      )
        fail('les éléments doivent être uniques');
      if (s.items) v.forEach((x, i) => check(s.items!, x, `${path}[${i}]`));
    } else if (s.type === 'string') {
      if (typeof v !== 'string') {
        fail('texte requis');
        return;
      }
      if (s.minLength !== undefined && v.length < s.minLength)
        fail(`au moins ${s.minLength} caractère(s)`);
      if (s.maxLength !== undefined && v.length > s.maxLength)
        fail(`au plus ${s.maxLength} caractère(s)`);
    } else if (s.type === 'number' || s.type === 'integer') {
      if (typeof v !== 'number' || !Number.isFinite(v)) {
        fail('nombre requis');
        return;
      }
      if (s.type === 'integer' && !Number.isInteger(v)) fail('entier requis');
      if (s.minimum !== undefined && v < s.minimum)
        fail(`minimum : ${s.minimum}`);
      if (s.maximum !== undefined && v > s.maximum)
        fail(`maximum : ${s.maximum}`);
    } else if (s.type === 'boolean' && typeof v !== 'boolean')
      fail('booléen requis');
  }
  check(schema, data, 'Configuration');
  return errors;
}
const names: Record<string, string> = {
  accept_documents: 'Accepter les documents',
  chat_id: 'Identifiant du chat',
  text: 'Message',
  query: 'Recherche Gmail',
  max_results: 'Nombre de messages',
  to: 'Destinataire',
  subject: 'Objet',
  body: 'Contenu',
  html: 'Contenu HTML',
  spreadsheet_id: 'Classeur Google Sheets',
  range: 'Feuille et plage',
  values: 'Valeurs de la ligne',
  confidence_threshold: 'Seuil de confiance',
  classes: 'Classes',
  prompt: 'Prompt',
  temperature: 'Température',
  field: 'Champ à comparer',
  operator: 'Opérateur',
  value: 'Valeur comparée',
  url: 'URL',
  method: 'Méthode HTTP',
  mapping: 'Mapping de sortie',
};
export function SchemaForm({
  schema,
  value,
  onChange,
  onValidity,
  suggestions = {},
}: {
  suggestions?: Record<string, { value: string; label: string }[]>;
  schema: Schema;
  value: Obj;
  onChange: (value: Obj) => void;
  onValidity?: (valid: boolean) => void;
}) {
  return (
    <div className="schema-form">
      {Object.entries(schema.properties || {}).map(([key, field]) => {
        const label =
          (names[key] || key) + (schema.required?.includes(key) ? ' *' : '');
        const set = (v: Json) => onChange({ ...value, [key]: v });
        if (field.type === 'boolean')
          return (
            <label key={key} className="check-field">
              <input
                aria-label={label}
                type="checkbox"
                checked={value[key] === true}
                onChange={(e) => set(e.target.checked)}
              />
              {label}
            </label>
          );
        if (field.type === 'array' || field.type === 'object' || !field.type)
          return (
            <div key={key}>
              <JsonEditor
                expected={
                  field.type === 'array'
                    ? 'array'
                    : field.type === 'object'
                      ? 'object'
                      : 'any'
                }
                label={label}
                value={
                  value[key] ??
                  (field.type === 'array'
                    ? []
                    : field.type === 'object'
                      ? {}
                      : null)
                }
                onChange={(v) => set(v as Json)}
                onValidity={onValidity}
              />
              {field.type === 'array' && (
                <small>
                  Format JSON, par exemple ["facture", "contrat", "cv"].
                </small>
              )}
            </div>
          );
        return (
          <label className="field" key={key}>
            <span>{label}</span>
            {field.enum ? (
              <select
                aria-label={label}
                value={String(value[key] ?? '')}
                onChange={(e) => set(e.target.value)}
              >
                <option value="">Choisir…</option>
                {field.enum.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            ) : (
              <input
                aria-label={label}
                list={suggestions[key]?.length ? `suggest-${key}` : undefined}
                type={
                  field.type === 'number' || field.type === 'integer'
                    ? 'number'
                    : 'text'
                }
                step={field.type === 'integer' ? 1 : 'any'}
                min={field.minimum}
                max={field.maximum}
                minLength={field.minLength}
                maxLength={field.maxLength}
                required={schema.required?.includes(key)}
                value={String(value[key] ?? '')}
                placeholder={
                  key === 'range'
                    ? 'Documents!A:D'
                    : key === 'text'
                      ? '{{ input.text }}'
                      : undefined
                }
                onChange={(e) => {
                  if (!e.target.value) {
                    const next = { ...value };
                    delete next[key];
                    onChange(next);
                  } else
                    set(
                      field.type === 'number' || field.type === 'integer'
                        ? Number(e.target.value)
                        : e.target.value,
                    );
                }}
              />
            )}
            {!!suggestions[key]?.length && (
              <datalist id={`suggest-${key}`}>
                {suggestions[key].map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </datalist>
            )}
            {field.description && <small>{field.description}</small>}
          </label>
        );
      })}
    </div>
  );
}
