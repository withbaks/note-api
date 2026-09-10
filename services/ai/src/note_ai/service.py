"""AI processing pipeline for memory objects."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_ai.fact_keys import (
    ACTION_HELPER_KEYS,
    is_action_suggestion,
    normalize_knowledge_fact_key,
)
from note_ai.fact_resolve import FactCandidate, embed_fact_sentence, resolve_fact_candidate
from note_ai.heuristics import HeuristicHelper, run_heuristics
from note_ai.llm_payload import build_llm_payload
from note_ai.llm_provider import circuit_is_open, complete_json, embed_texts
from note_ai.prompts import extract_prompt, suggest_prompt, understand_prompt, validation_retry_suffix
from note_ai.schemas import AISchemaOutput, ExtractResult, SuggestResult, UnderstandingResult
from note_ai.thread_resolve import resolve_thread
from note_core.config import get_settings
from note_core.hlc import HLCClock
from note_core.sync_constants import (
    confidence_threshold_for,
    HIGH_CONFIDENCE_THRESHOLD,
    REVIEW_CONFIDENCE_THRESHOLD,
)
from note_helpers.facts import FACT_FROM_HELPER
from note_sync.server_sync import ServerSyncEmitter
from note_db.models import (
    AIJob,
    AIState,
    Category,
    CategorySource,
    Entity,
    Helper,
    HelperSource,
    HelperStatus,
    MemoryCategory,
    MemoryObject,
    MemoryPerson,
    MemoryPersonRole,
    MemoryType,
    Understanding,
    User,
)
from note_db.people import PeopleService

SCHEDULED_HELPER_KEYS = frozenset({"is_reminder", "is_birthday", "is_calendar"})
FACT_KEYS_FROM_HELPERS = FACT_FROM_HELPER

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d/%m/%y",
    "%m/%d/%y",
    "%d-%m-%y",
    "%m-%d-%y",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d",
    "%b %d",
    "%d %B",
    "%d %b",
)


def _try_parse_scheduled_at(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    cleaned = re.sub(r"^(on\s+)", "", cleaned, flags=re.IGNORECASE).strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if parsed.year == 1900:
                parsed = parsed.replace(year=datetime.now(UTC).year)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            continue
    return None


class AIService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()
        self._server_clock = HLCClock("server")

    async def process_memory(self, memory_object_id: str) -> None:
        obj = await self.session.get(MemoryObject, UUID(memory_object_id))
        if not obj:
            return

        user = await self.session.get(User, obj.user_id)
        if not user:
            return

        job = await self.session.scalar(
            select(AIJob)
            .where(AIJob.memory_object_id == obj.id)
            .order_by(AIJob.created_at.desc())
        )
        if not job:
            job = AIJob(memory_object_id=obj.id, state=AIState.captured)
            self.session.add(job)
            await self.session.flush()

        job.state = AIState.processing
        job.attempts += 1
        obj.ai_state = AIState.processing

        try:
            text = self._extract_text(obj)
            content_hash = self._content_hash(text)
            hash_changed = obj.content_hash != content_hash
            obj.content_hash = content_hash

            resume_from = AIState.captured if hash_changed else obj.ai_state
            heuristic_helpers, heuristic_meta = run_heuristics(text)

            if not user.ai_enabled:
                await self._process_heuristics_only(
                    obj, job, user, text, heuristic_helpers, heuristic_meta
                )
                return

            llm_result: dict | None = None
            if resume_from in (AIState.captured, AIState.processing, AIState.failed):
                llm_result = await self._run_llm(
                    text,
                    heuristic_helpers,
                    heuristic_meta,
                    user_id=user.id,
                    memory_id=obj.id,
                )
                job.llm_result_cache = llm_result
                await self._store_understanding(obj, llm_result)
                obj.ai_state = AIState.understood
                job.state = AIState.understood
                job.last_completed_step = "understood"
                await self.session.flush()
                await self.session.commit()
            else:
                llm_result = job.llm_result_cache or await self._load_cached_llm_result(obj)

            if resume_from in (
                AIState.captured,
                AIState.processing,
                AIState.failed,
                AIState.understood,
            ):
                await self._store_people(user, llm_result.get("people", []))
                await self._store_memory_people(obj, user, llm_result.get("people", []))
                await self._store_suggestions(
                    obj, heuristic_helpers, llm_result, user, text
                )
                await self._store_categories(obj, llm_result.get("suggested_categories", []))
                obj.ai_state = AIState.organized
                job.state = AIState.organized
                job.last_completed_step = "organized"
                await self.session.flush()
                await self.session.commit()

            user_id = obj.user_id
            memory_id = obj.id
            if resume_from != AIState.indexed or hash_changed:
                await self._store_entities(obj, llm_result.get("entities", []))
                await self._resolve_thread_for_memory(obj)
                from note_search.search_index import SearchIndexService

                await SearchIndexService(self.session).index_memory(obj.id)
                await SearchIndexService(self.session).index_user_profile(obj.user_id)
                obj.ai_state = AIState.indexed
                job.state = AIState.indexed
                job.last_completed_step = "indexed"
                job.last_error = None
                await self.session.flush()
                await self.session.commit()

            await ServerSyncEmitter(self.session).emit_memory_ai_artifacts(user_id, memory_id)
        except Exception as exc:
            obj.ai_state = AIState.failed
            job.state = AIState.failed
            job.last_error = str(exc)
            if job.attempts < job.max_attempts:
                job.next_retry_at = datetime.now(UTC) + timedelta(minutes=2**job.attempts)

    async def _process_heuristics_only(
        self,
        obj: MemoryObject,
        job: AIJob,
        user: User,
        text: str,
        heuristic_helpers: list[HeuristicHelper],
        heuristic_meta: dict,
    ) -> None:
        """Fully local server path when ai_enabled=false — no OpenAI calls."""
        llm_result = self._fallback_llm(text, heuristic_helpers, heuristic_meta)
        job.llm_result_cache = llm_result
        await self._store_understanding(obj, llm_result)
        obj.ai_state = AIState.understood
        job.state = AIState.understood
        job.last_completed_step = "understood"
        await self.session.flush()

        await self._store_people(user, llm_result.get("people", []))
        await self._store_memory_people(obj, user, llm_result.get("people", []))
        await self._store_suggestions(obj, heuristic_helpers, llm_result, user, text)
        obj.ai_state = AIState.organized
        job.state = AIState.organized
        job.last_completed_step = "organized"
        await self.session.flush()

        # FTS-only search docs (no embeddings when OpenAI is unused)
        from note_search.search_index import SearchIndexService

        await SearchIndexService(self.session).index_memory(obj.id)
        await self._resolve_thread_for_memory(obj)

        obj.ai_state = AIState.indexed
        job.state = AIState.indexed
        job.last_completed_step = "indexed"
        job.last_error = None
        user_id = obj.user_id
        memory_id = obj.id
        await self.session.flush()
        await self.session.commit()
        await ServerSyncEmitter(self.session).emit_memory_ai_artifacts(user_id, memory_id)

    @staticmethod
    def _content_hash(text: str) -> str:
        normalized = " ".join(text.split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def _load_cached_llm_result(self, obj: MemoryObject) -> dict:
        understanding = await self.session.scalar(
            select(Understanding).where(
                Understanding.memory_object_id == obj.id,
                Understanding.superseded_by.is_(None),
            )
        )
        if understanding and understanding.raw:
            return understanding.raw
        return {}

    async def retry_memory(self, user_id: UUID, memory_id: UUID) -> None:
        obj = await self.session.get(MemoryObject, memory_id)
        if not obj or obj.user_id != user_id:
            raise ValueError("Memory not found")
        obj.ai_state = AIState.captured
        job = AIJob(memory_object_id=obj.id, state=AIState.captured)
        self.session.add(job)
        await self.session.flush()
        await self.process_memory(str(memory_id))

    def _extract_text(self, obj: MemoryObject) -> str:
        if obj.type == MemoryType.structured:
            return f"{obj.structured_title}: {obj.structured_value}"
        if obj.type == MemoryType.bookmark:
            meta = obj.link_metadata if isinstance(obj.link_metadata, dict) else {}
            parts: list[str | None] = [
                obj.structured_title,
                meta.get("description") if meta else None,
                meta.get("extracted_text")[:4000] if meta and meta.get("extracted_text") else None,
                obj.structured_value,
                obj.content_text,
            ]
            return "\n".join(p for p in parts if p) or ""
        if obj.type == MemoryType.image:
            caption = obj.content_text or "Image memory"
            return f"{caption}\n{obj.media_uri or ''}".strip()
        return obj.content_text or ""

    async def _run_llm(
        self,
        text: str,
        heuristic_helpers: list[HeuristicHelper],
        heuristic_meta: dict,
        *,
        user_id: UUID | None = None,
        memory_id: UUID | None = None,
    ) -> dict:
        if not self.settings.openai_api_key or circuit_is_open():
            return self._fallback_llm(text, heuristic_helpers, heuristic_meta)

        redacted_text, heuristic_json, _placeholders = build_llm_payload(text, heuristic_helpers)

        try:
            understand = await self._call_with_validation(
                understand_prompt(redacted_text, heuristic_json),
                UnderstandingResult,
                user_id=user_id,
                memory_id=memory_id,
                operation="understand",
            )
            extract = await self._call_with_validation(
                extract_prompt(redacted_text, heuristic_json),
                ExtractResult,
                user_id=user_id,
                memory_id=memory_id,
                operation="extract",
            )
            suggest = await self._call_with_validation(
                suggest_prompt(redacted_text, heuristic_json),
                SuggestResult,
                user_id=user_id,
                memory_id=memory_id,
                operation="suggest",
            )
            merged = AISchemaOutput(
                **understand.model_dump(),
                people=extract.people,
                entities=extract.entities,
                suggestions=suggest.suggestions,
                suggested_categories=suggest.suggested_categories,
            )
            return merged.to_store_dict()
        except Exception:
            return self._fallback_llm(text, heuristic_helpers, heuristic_meta)

    async def _call_with_validation(
        self,
        prompt: str,
        schema: type,
        *,
        user_id: UUID | None = None,
        memory_id: UUID | None = None,
        operation: str = "llm",
    ):
        last_error: ValidationError | None = None
        current_prompt = prompt
        for _ in range(2):
            content = await complete_json(
                current_prompt,
                session=self.session,
                user_id=user_id,
                memory_id=memory_id,
                operation=operation,
            )
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = exc
                current_prompt = prompt + validation_retry_suffix(str(exc))
        if last_error:
            raise last_error
        raise RuntimeError("LLM validation failed")

    def _fallback_llm(
        self, text: str, heuristic_helpers: list[HeuristicHelper], heuristic_meta: dict
    ) -> dict:
        words = [w.strip(".,!?") for w in text.split() if len(w) > 3][:5]
        people = []
        for h in heuristic_helpers:
            if h.subject == "named" and h.display_name:
                people.append({"display_name": h.display_name, "relationship": h.relationship})
        return {
            "summary": text[:200],
            "tags": words[:3],
            "topics": words[:2],
            "connections": [],
            "why_it_matters": "Personal memory captured for future reference.",
            "keyword_summary": ", ".join(words),
            "people": people,
            "suggestions": [
                {
                    "kind": "action" if h.key in SCHEDULED_HELPER_KEYS else "knowledge",
                    "title": h.key.replace("is_", "").replace("_", " ").title(),
                    "description": None,
                    "value": h.value,
                    "person_ref": "self" if h.subject == "self" else (h.display_name or h.person),
                    "semantic_type": h.semantic_type,
                    "relationship": h.relationship,
                    "proposed_fact_key": FACT_KEYS_FROM_HELPERS[h.key][0]
                    if h.key in FACT_KEYS_FROM_HELPERS
                    else h.key.replace("is_", ""),
                    "key": h.key,
                    "confidence": 0.85,
                }
                for h in heuristic_helpers
            ],
            "entities": heuristic_meta.get("entities", []),
            "suggested_categories": [],
        }

    @staticmethod
    def _find_span(text: str, needle: str | None) -> tuple[int | None, int | None]:
        if not needle:
            return None, None
        idx = text.lower().find(needle.strip().lower())
        if idx < 0:
            return None, None
        return idx, idx + len(needle)

    async def _store_people(self, user: User, people_data: list[dict]) -> None:
        svc = PeopleService(self.session)
        for item in people_data:
            name = (item.get("display_name") or "").strip()
            if not name:
                continue
            await svc.resolve(
                user.id,
                display_name=name,
                relationship=item.get("relationship"),
                subject_self=False,
                user=user,
            )

    async def _store_memory_people(
        self, obj: MemoryObject, user: User, people_data: list[dict]
    ) -> None:
        for item in people_data:
            name = (item.get("display_name") or "").strip()
            if not name:
                continue
            person = await self._resolve_person_ref(user, name, item.get("relationship"))
            role = item.get("role") or MemoryPersonRole.mentioned.value
            existing = await self.session.scalar(
                select(MemoryPerson).where(
                    MemoryPerson.memory_object_id == obj.id,
                    MemoryPerson.person_id == person.id,
                    MemoryPerson.role == role,
                )
            )
            if not existing:
                self.session.add(
                    MemoryPerson(
                        memory_object_id=obj.id,
                        person_id=person.id,
                        role=role,
                    )
                )

    def _normalize_suggestions(
        self,
        heuristic_helpers: list[HeuristicHelper],
        llm_result: dict,
        text: str,
    ) -> list[dict]:
        suggestions = list(llm_result.get("suggestions") or [])
        if not suggestions:
            # Legacy helpers + facts → suggestions (no direct fact writes)
            for item in llm_result.get("helpers") or []:
                key = item.get("key") or "suggestion"
                suggestions.append(
                    {
                        "kind": "system" if key in SCHEDULED_HELPER_KEYS else "knowledge",
                        "title": item.get("title") or key.replace("is_", "").replace("_", " ").title(),
                        "description": item.get("description"),
                        "value": item.get("value"),
                        "person_ref": item.get("person_ref") or item.get("person"),
                        "relationship": item.get("relationship"),
                        "semantic_type": item.get("semantic_type"),
                        "proposed_fact_key": item.get("proposed_fact_key")
                        or (
                            FACT_KEYS_FROM_HELPERS[key][0]
                            if key in FACT_KEYS_FROM_HELPERS
                            else key.replace("is_", "")
                        ),
                        "key": key,
                        "tool": item.get("tool"),
                        "metadata": item.get("metadata"),
                    }
                )
            for fact in llm_result.get("facts") or []:
                key = fact.get("key")
                if not key:
                    continue
                suggestions.append(
                    {
                        "kind": "knowledge",
                        "title": fact.get("display_label") or key.replace("_", " ").title(),
                        "description": f"Save {fact.get('value')}?",
                        "value": fact.get("value"),
                        "person_ref": fact.get("person_ref") or fact.get("person"),
                        "relationship": fact.get("relationship"),
                        "semantic_type": fact.get("semantic_type"),
                        "proposed_fact_key": key,
                        "fact_type": fact.get("fact_type", "attribute"),
                        "key": f"is_{key}",
                    }
                )
            if not suggestions:
                for h in heuristic_helpers:
                    suggestions.append(
                        {
                            "kind": "system" if h.key in SCHEDULED_HELPER_KEYS else "knowledge",
                            "title": h.key.replace("is_", "").replace("_", " ").title(),
                            "value": h.value,
                            "person_ref": "self" if h.subject == "self" else (h.display_name or h.person),
                            "relationship": h.relationship,
                            "semantic_type": h.semantic_type,
                            "proposed_fact_key": FACT_KEYS_FROM_HELPERS[h.key][0]
                            if h.key in FACT_KEYS_FROM_HELPERS
                            else h.key.replace("is_", ""),
                            "key": h.key,
                        }
                    )
        for s in suggestions:
            span_text = s.get("span_text") or s.get("value")
            start, end = self._find_span(text, span_text if isinstance(span_text, str) else None)
            s.setdefault("span_start", start)
            s.setdefault("span_end", end)
        return suggestions

    async def _store_suggestions(
        self,
        obj: MemoryObject,
        heuristic_helpers: list[HeuristicHelper],
        llm_result: dict,
        user: User,
        text: str,
    ) -> None:
        suggestions = self._normalize_suggestions(heuristic_helpers, llm_result, text)
        await self._store_helpers_from_suggestions(obj, suggestions, user, heuristic_helpers, bool(llm_result.get("suggestions") or llm_result.get("helpers")))

    async def _store_helpers_from_suggestions(
        self,
        obj: MemoryObject,
        suggestions: list[dict],
        user: User,
        heuristic_helpers: list[HeuristicHelper],
        from_llm: bool,
    ) -> None:
        dismissed = await self.session.scalars(
            select(Helper).where(
                Helper.memory_object_id == obj.id,
                Helper.status == HelperStatus.dismissed,
            )
        )
        dismissed_keys = {h.key for h in dismissed}
        heuristic_by_key = {h.key: h for h in heuristic_helpers}
        source_default = HelperSource.llm if from_llm else HelperSource.heuristic
        seen_keys: set[str] = set()

        for item in suggestions:
            raw_key = item.get("key") or item.get("proposed_fact_key") or "suggestion"
            kind = item.get("kind") or (
                "action" if raw_key in SCHEDULED_HELPER_KEYS or raw_key in ACTION_HELPER_KEYS else "knowledge"
            )
            if kind == "system":
                kind = "action"

            person_ref = item.get("person_ref") or item.get("person")
            relationship = item.get("relationship")
            heuristic_h = heuristic_by_key.get(item.get("key") or "")
            if relationship is None and heuristic_h:
                relationship = heuristic_h.relationship
            if person_ref is None and heuristic_h:
                person_ref = (
                    "self" if heuristic_h.subject == "self" else heuristic_h.display_name
                )

            person = await self._resolve_person_ref(user, person_ref, relationship)
            value = item.get("value")
            if value is None and heuristic_h:
                value = heuristic_h.value
            semantic_type = item.get("semantic_type") or (
                heuristic_h.semantic_type if heuristic_h else None
            )
            title = item.get("title") or raw_key.replace("is_", "").replace("_", " ").title()
            description = item.get("description")
            proposed_fact_key = normalize_knowledge_fact_key(
                item.get("proposed_fact_key") or (
                    FACT_KEYS_FROM_HELPERS[raw_key][0] if raw_key in FACT_KEYS_FROM_HELPERS else None
                ),
                semantic_type=semantic_type,
            )
            tool_name = item.get("tool")
            metadata = item.get("metadata") or {}
            if item.get("fact_type"):
                metadata = {**metadata, "fact_type": item.get("fact_type")}

            confidence_raw = item.get("confidence")
            try:
                confidence = float(confidence_raw) if confidence_raw is not None else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = self._apply_trust_bias(user, semantic_type, confidence)

            person_label = None if person.relationship == "self" else person.display_name.lower()
            server_hlc = self._server_clock.now().to_string()

            # Knowledge: agent-side fact write via resolution — no Save chip
            if kind == "knowledge" or (
                not is_action_suggestion(raw_key, kind, tool_name) and proposed_fact_key
            ):
                if not proposed_fact_key or not value:
                    continue
                high_threshold = confidence_threshold_for(raw_key, semantic_type)
                if confidence < REVIEW_CONFIDENCE_THRESHOLD and not (
                    user.helper_auto_accept and source_default == HelperSource.heuristic
                ):
                    continue

                embedding = await embed_fact_sentence(
                    proposed_fact_key,
                    str(value),
                    person_label,
                    session=self.session,
                    user_id=user.id,
                    memory_id=obj.id,
                )
                result = await resolve_fact_candidate(
                    self.session,
                    user_id=user.id,
                    memory_id=obj.id,
                    candidate=FactCandidate(
                        fact_key=proposed_fact_key,
                        value=str(value),
                        person_id=person.id,
                        person_label=person_label,
                        display_label=title,
                        fact_type=(metadata or {}).get("fact_type", "attribute"),
                        semantic_type=semantic_type,
                        confidence=confidence,
                    ),
                    embedding=embedding,
                    allow_conflict_overwrite=confidence >= high_threshold,
                )
                # Mid-confidence conflict → review chip ("Still true?" style)
                if result and result.conflict:
                    review_key = f"review_fact:{proposed_fact_key}"
                    if review_key in dismissed_keys:
                        continue
                    seen_keys.add(review_key)
                    existing = await self.session.scalar(
                        select(Helper).where(
                            Helper.memory_object_id == obj.id, Helper.key == review_key
                        )
                    )
                    review_title = f"Still true? {title}"
                    review_desc = (
                        f"New: {value}. Existing: {result.existing_value}"
                    )
                    review_meta = {
                        **metadata,
                        "fact_key": proposed_fact_key,
                        "existing_value": result.existing_value,
                        "new_value": value,
                        "review_kind": "fact_conflict",
                    }
                    if existing and existing.status != HelperStatus.dismissed:
                        existing.title = review_title
                        existing.description = review_desc
                        existing.kind = "action"
                        existing.proposed_fact_key = proposed_fact_key
                        existing.value = str(value)
                        existing.person = person_label
                        existing.person_id = person.id
                        existing.semantic_type = semantic_type
                        existing.helper_metadata = review_meta
                        existing.tool_name = "review_fact"
                        existing.confidence = confidence
                        existing.status = HelperStatus.suggested
                        existing.hlc = server_hlc
                    elif not existing:
                        self.session.add(
                            Helper(
                                memory_object_id=obj.id,
                                key=review_key,
                                title=review_title,
                                description=review_desc,
                                kind="action",
                                proposed_fact_key=proposed_fact_key,
                                value=str(value),
                                person=person_label,
                                person_id=person.id,
                                semantic_type=semantic_type,
                                helper_metadata=review_meta,
                                tool_name="review_fact",
                                confidence=confidence,
                                source=source_default,
                                status=HelperStatus.suggested,
                                hlc=server_hlc,
                            )
                        )
                continue

            # Action helpers → chips only
            key = raw_key if raw_key in ACTION_HELPER_KEYS or raw_key in SCHEDULED_HELPER_KEYS else (
                item.get("key") or tool_name or "action"
            )
            if key in dismissed_keys:
                continue
            seen_keys.add(key)

            scheduled_at = None
            if key in SCHEDULED_HELPER_KEYS or tool_name in (
                "create_reminder",
                "create_calendar_event",
            ):
                if heuristic_h and heuristic_h.scheduled_at:
                    scheduled_at = heuristic_h.scheduled_at
                elif metadata.get("scheduled_at"):
                    scheduled_at = _try_parse_scheduled_at(str(metadata.get("scheduled_at")))
                else:
                    scheduled_at = _try_parse_scheduled_at(value)

            existing = await self.session.scalar(
                select(Helper).where(Helper.memory_object_id == obj.id, Helper.key == key)
            )
            if existing and existing.status == HelperStatus.dismissed:
                continue

            status = HelperStatus.suggested
            if existing:
                existing.title = title
                existing.description = description
                existing.kind = "action"
                existing.proposed_fact_key = None
                existing.span_start = item.get("span_start")
                existing.span_end = item.get("span_end")
                existing.confidence = confidence
                existing.helper_metadata = metadata or None
                existing.tool_name = tool_name
                existing.value = value
                existing.person = person_label
                existing.person_id = person.id
                existing.semantic_type = semantic_type
                existing.source = source_default
                existing.hlc = server_hlc
                if scheduled_at is not None:
                    existing.scheduled_at = scheduled_at
                if existing.status != HelperStatus.accepted:
                    existing.status = status
            else:
                self.session.add(
                    Helper(
                        memory_object_id=obj.id,
                        key=key,
                        title=title,
                        description=description,
                        kind="action",
                        proposed_fact_key=None,
                        span_start=item.get("span_start"),
                        span_end=item.get("span_end"),
                        confidence=confidence,
                        helper_metadata=metadata or None,
                        tool_name=tool_name,
                        value=value,
                        person=person_label,
                        person_id=person.id,
                        semantic_type=semantic_type,
                        source=source_default,
                        status=status,
                        scheduled_at=scheduled_at,
                        hlc=server_hlc,
                    )
                )

        if from_llm:
            for h in heuristic_helpers:
                if h.key in seen_keys or h.key in dismissed_keys:
                    continue
                existing = await self.session.scalar(
                    select(Helper).where(Helper.memory_object_id == obj.id, Helper.key == h.key)
                )
                if existing and existing.status == HelperStatus.suggested:
                    existing.status = HelperStatus.dismissed

    async def _resolve_thread_for_memory(self, obj: MemoryObject) -> None:
        async def _embed(texts: list[str]):
            return await embed_texts(
                texts,
                session=self.session,
                user_id=obj.user_id,
                memory_id=obj.id,
                operation="thread_embed",
            )

        async def _complete(prompt: str):
            return await complete_json(
                prompt,
                session=self.session,
                user_id=obj.user_id,
                memory_id=obj.id,
                operation="thread_summary",
            )

        await resolve_thread(
            self.session,
            obj,
            embed_fn=_embed,
            complete_json_fn=_complete,
        )

    async def _resolve_person_ref(
        self, user: User, person_ref: str | None, relationship: str | None = None
    ):
        people = PeopleService(self.session)
        ref = (person_ref or "self").strip()
        if ref.lower() in {"self", "me", "i", "you", ""}:
            return await people.ensure_self(user)
        return await people.resolve(
            user.id,
            display_name=ref,
            relationship=relationship,
            subject_self=False,
            user=user,
        )

    async def _store_understanding(self, obj: MemoryObject, result: dict) -> None:
        existing = await self.session.scalar(
            select(Understanding).where(
                Understanding.memory_object_id == obj.id,
                Understanding.superseded_by.is_(None),
            )
        )
        new_id = uuid4()
        understanding = Understanding(
            id=new_id,
            memory_object_id=obj.id,
            summary=result.get("summary"),
            tags=result.get("tags"),
            topics=result.get("topics"),
            connections=result.get("connections"),
            why_it_matters=result.get("why_it_matters"),
            keyword_summary=result.get("keyword_summary"),
            raw=result,
        )
        if existing:
            existing.superseded_by = new_id
        self.session.add(understanding)

    def _apply_trust_bias(self, user: User, semantic_type: str | None, confidence: float) -> float:
        trust = user.helper_trust if isinstance(user.helper_trust, dict) else {}
        entry = trust.get(semantic_type or "unknown") or {}
        accepted = int(entry.get("accepted", 0))
        dismissed = int(entry.get("dismissed", 0))
        total = accepted + dismissed
        if total < 3:
            return confidence
        bias = (accepted - dismissed) / total * 0.1
        return max(0.0, min(1.0, confidence + bias))

    async def _store_entities(self, obj: MemoryObject, entities: list[dict]) -> None:
        await self.session.execute(delete(Entity).where(Entity.memory_object_id == obj.id))
        created: list[Entity] = []
        for ent in entities:
            value = (ent.get("value") or "")[:512]
            if not value:
                continue
            entity = Entity(
                memory_object_id=obj.id,
                user_id=obj.user_id,
                entity_type=ent.get("entity_type", "unknown"),
                value=value,
            )
            self.session.add(entity)
            created.append(entity)

        memory_text = self._extract_text(obj)
        if memory_text:
            summary = Entity(
                memory_object_id=obj.id,
                user_id=obj.user_id,
                entity_type="memory_summary",
                value=memory_text[:512],
            )
            self.session.add(summary)
            created.append(summary)

        await self.session.flush()

        if not created:
            return

        from note_ai.llm_provider import embed_texts

        inputs = [e.value for e in created]
        embeddings = await embed_texts(
            inputs,
            session=self.session,
            user_id=obj.user_id,
            memory_id=obj.id,
            operation="entity_embed",
        )
        for entity, emb in zip(created, embeddings, strict=True):
            entity.embedding = emb

    async def _store_categories(self, obj: MemoryObject, suggested: list[str]) -> None:
        for name in suggested[:3]:
            cat = await self.session.scalar(
                select(Category).where(Category.user_id == obj.user_id, Category.name == name)
            )
            if not cat:
                cat = Category(user_id=obj.user_id, name=name, source=CategorySource.ai)
                self.session.add(cat)
                await self.session.flush()

            existing_link = await self.session.scalar(
                select(MemoryCategory).where(
                    MemoryCategory.memory_object_id == obj.id,
                    MemoryCategory.category_id == cat.id,
                )
            )
            user_link = await self.session.scalar(
                select(MemoryCategory).where(
                    MemoryCategory.memory_object_id == obj.id,
                    MemoryCategory.source == CategorySource.user,
                )
            )
            if not existing_link and not user_link:
                self.session.add(
                    MemoryCategory(
                        memory_object_id=obj.id,
                        category_id=cat.id,
                        source=CategorySource.ai,
                    )
                )
