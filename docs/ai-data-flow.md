# AI data flow

This document describes what user data leaves the device when AI is enabled.

## When `ai_enabled=true` (default)

1. **Capture** — Raw note text syncs to Postgres via the normal sync push.
2. **Heuristics** — Regex runs on the server before any LLM call (phone, age, reminders, etc.).
3. **LLM prompts** — Three schema-scoped calls per memory:
   - `understand` — summary, tags, topics, connections
   - `extract` — people and entities
   - `suggest` — helpers and categories
4. **PII minimization** — Values already extracted by regex (phone, age, budget, shoe size) are
   replaced with placeholders in the prompt. Heuristic JSON uses `[redacted]` for those fields.
   Media URIs are stripped from prompts.
5. **Embeddings** — Chunk text, entity values, and search index docs are sent to OpenAI
   `text-embedding-3-small` when indexing.
6. **Smart search** — Query text and retrieved snippets may be sent for answer composition.

## When `ai_enabled=false`

- No OpenAI API calls (no LLM, no embeddings).
- Server runs heuristics-only analysis and stores helpers locally.
- Search index is not embedded (keyword/FTS only on server; local SQLite FTS still works).

## Retention

- Understandings store the full LLM JSON in `understandings.raw` for replay on retry.
- `ai_usage_events` logs token counts and estimated cost per operation.
- Users can delete memories; cascade deletion removes derived rows and emits tombstone sync mutations.

## Models

- Chat: configured via `AI_MODEL` (default `gpt-4o-mini`).
- Embeddings: `text-embedding-3-small`.
