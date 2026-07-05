/*
 * supervisor.ts — the human supervisor's actor identity for the one governed write.
 *
 * POST /beliefs/{id}/invalidate requires a well-formed, non-nil actor_id. Per the backend
 * design (app/schemas.py InvalidateRequest), the supervisor is deliberately NOT a fleet agent:
 * invalidation is the one action a human takes, so this id is intentionally not an `agents`
 * row and is not validated against that table. A fixed non-nil constant keeps audit_log rows
 * attributable to "the console supervisor" across a session (the nil UUID would be rejected 422).
 */

import type { UUID } from "../api/types";

export const SUPERVISOR_ACTOR: UUID = "5e5e0000-0000-4000-8000-000000000001";
