import type { Persona } from "./types";

export const PERSONA_FIELDS = [
  "name",
  "identity",
  "voice",
  "interests",
  "boundaries",
] as const;
export type PersonaField = (typeof PERSONA_FIELDS)[number];
export const PERSONA_LABELS: Record<PersonaField, string> = {
  name: "角色名字",
  identity: "它是谁",
  voice: "说话的方式",
  interests: "兴趣与话题",
  boundaries: "不想涉及的内容",
};

export type FieldChange = {
  field: PersonaField;
  label: string;
  before: string;
  after: string;
};
export type PersonaDiff = { changed: FieldChange[]; unchanged: PersonaField[] };

/** Field-level comparison of two persona bodies.
 *
 * `version` is deliberately not compared: every save rewrites it, so including
 * it would mark every pair of versions as different. The API hands the panel
 * every version body already (`GET /persona/versions`), which is why the diff
 * lives here instead of behind a route the connection assistant would have to
 * allow — no new local API surface, no extension update.
 */
export function comparePersona(before: Persona, after: Persona): PersonaDiff {
  const changed: FieldChange[] = [];
  const unchanged: PersonaField[] = [];
  for (const field of PERSONA_FIELDS) {
    const left = before[field] ?? "";
    const right = after[field] ?? "";
    if (left === right) {
      unchanged.push(field);
    } else {
      changed.push({
        field,
        label: PERSONA_LABELS[field],
        before: left,
        after: right,
      });
    }
  }
  return { changed, unchanged };
}

/** True when two bodies agree on every editable field, ignoring `version`. */
export function sameBody(left: Persona, right: Persona): boolean {
  return PERSONA_FIELDS.every(
    (field) => (left[field] ?? "") === (right[field] ?? ""),
  );
}

/** A body restored from `target`, carrying the next version number.
 *
 * Rollback is append-only: `PUT /persona` writes a new row, so the history the
 * user is looking at stays intact and the restored content becomes a *new*
 * version. Keeping that arithmetic in one place stops the page from writing a
 * body whose `version` disagrees with the server's expectation (409).
 */
export function rollbackBody(target: Persona, current: Persona): Persona {
  return { ...target, version: current.version };
}
