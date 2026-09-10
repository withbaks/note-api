"""Structured queries over people, facts, friendships, and temporal data."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Friendship, Helper, HelperStatus, MemoryFact, MemoryObject, Person, User
from note_search.service import SearchResult

_AGGREGATE_PATTERNS = (
    r"\bhow many\b",
    r"\bhow much\b",
    r"\bcount\b",
    r"\bnumber of\b",
)
_SOCIAL_PATTERNS = (
    r"\bfriends?\b",
    r"\bfriend list\b",
    r"\bwho are my friends\b",
    r"\bpending requests?\b",
)
_INVENTORY_PATTERNS = (
    r"\ball my\b",
    r"\blist all\b",
    r"\beverything about\b",
    r"\bwhat do i know about\b",
    r"\bshow me all\b",
)
_TEMPORAL_PATTERNS = (
    r"\b\d+(st|nd|rd|th)?\s+month\b",
    r"\bin (january|february|march|april|may|june|july|august|september|october|november|december)\b",
    r"\bwhat.*remember\b",
    r"\bupcoming\b",
    r"\bscheduled\b",
    r"\bbirthday\b",
    r"\breminder\b",
)

_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


class QueryIntent(str, Enum):
    memory_recall = "memory_recall"
    aggregate = "aggregate"
    inventory = "inventory"
    social = "social"
    temporal = "temporal"


class StructuredAnswer(BaseModel):
    answer: str
    sources: list[SearchResult]
    pipeline: str


def classify_intent(query: str) -> QueryIntent:
    lower = query.strip().lower()
    if not lower:
        return QueryIntent.memory_recall

    if any(re.search(p, lower) for p in _AGGREGATE_PATTERNS):
        if re.search(r"\bfriends?\b", lower):
            return QueryIntent.aggregate
        if re.search(r"\bfacts?\b", lower):
            return QueryIntent.aggregate

    if any(re.search(p, lower) for p in _SOCIAL_PATTERNS):
        return QueryIntent.social

    if any(re.search(p, lower) for p in _TEMPORAL_PATTERNS):
        return QueryIntent.temporal

    if any(re.search(p, lower) for p in _INVENTORY_PATTERNS):
        return QueryIntent.inventory

    return QueryIntent.memory_recall


def _extract_month(query: str) -> int | None:
    lower = query.lower()
    month_match = re.search(r"\b(\d+)(?:st|nd|rd|th)?\s+month\b", lower)
    if month_match:
        month = int(month_match.group(1))
        if 1 <= month <= 12:
            return month
    for name, num in _MONTH_NAMES.items():
        if name in lower:
            return num
    return None


class _FriendDisplay(BaseModel):
    id: UUID
    display_name: str


class StructuredQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def answer(self, user_id: UUID, query: str, intent: QueryIntent) -> StructuredAnswer | None:
        if intent == QueryIntent.aggregate:
            return await self._aggregate(user_id, query)
        if intent == QueryIntent.social:
            return await self._social(user_id, query)
        if intent == QueryIntent.inventory:
            return await self._inventory(user_id, query)
        if intent == QueryIntent.temporal:
            return await self._temporal(user_id, query)
        return None

    async def _aggregate(self, user_id: UUID, query: str) -> StructuredAnswer | None:
        lower = query.lower()
        if re.search(r"\bfriends?\b", lower):
            friends = await self._list_accepted_friendships(user_id)
            count = len(friends)
            names = ", ".join(f.display_name for f in friends[:8])
            suffix = f": {names}" if names else ""
            if count > 8:
                suffix += f" and {count - 8} more"
            answer = f"You have {count} friend{'s' if count != 1 else ''}{suffix}."
            sources = [
                SearchResult(
                    source_type="friendship",
                    friendship_id=f.id,
                    content_preview=f.display_name,
                    match_type="social:friend",
                    score=1.0,
                )
                for f in friends[:12]
            ]
            return StructuredAnswer(answer=answer, sources=sources, pipeline="aggregate:friends")

        if re.search(r"\bfacts?\b", lower):
            facts = (
                await self.session.scalars(
                    select(MemoryFact).where(MemoryFact.user_id == user_id)
                )
            ).all()
            count = len(facts)
            answer = f"You have {count} saved fact{'s' if count != 1 else ''}."
            sources = [
                SearchResult(
                    source_type="fact",
                    fact_id=f.id,
                    content_preview=f"{f.display_label or f.key}: {f.value}",
                    match_type="aggregate:facts",
                    score=1.0,
                )
                for f in facts[:12]
            ]
            return StructuredAnswer(answer=answer, sources=sources, pipeline="aggregate:facts")

        return None

    async def _social(self, user_id: UUID, query: str) -> StructuredAnswer | None:
        lower = query.lower()
        if "pending" in lower:
            pending = await self._list_pending_friendships(user_id)
            if not pending:
                return StructuredAnswer(
                    answer="You have no pending friend requests.",
                    sources=[],
                    pipeline="social:pending",
                )
            names = ", ".join(p.display_name for p in pending)
            return StructuredAnswer(
                answer=f"You have {len(pending)} pending request(s): {names}.",
                sources=[
                    SearchResult(
                        source_type="friendship",
                        friendship_id=p.id,
                        content_preview=p.display_name,
                        match_type="social:pending",
                        score=1.0,
                    )
                    for p in pending
                ],
                pipeline="social:pending",
            )

        friends = await self._list_accepted_friendships(user_id)
        if not friends:
            return StructuredAnswer(
                answer="You don't have any friends on Note yet.",
                sources=[],
                pipeline="social:friends",
            )
        names = ", ".join(f.display_name for f in friends)
        return StructuredAnswer(
            answer=f"Your friends: {names}.",
            sources=[
                SearchResult(
                    source_type="friendship",
                    friendship_id=f.id,
                    content_preview=f.display_name,
                    match_type="social:friend",
                    score=1.0,
                )
                for f in friends
            ],
            pipeline="social:friends",
        )

    async def _inventory(self, user_id: UUID, query: str) -> StructuredAnswer | None:
        lower = query.lower()
        person_name = None
        about_match = re.search(r"(?:about|for)\s+(\w+)", lower)
        if about_match:
            person_name = about_match.group(1)

        stmt = select(MemoryFact).where(MemoryFact.user_id == user_id)
        if person_name:
            person = await self.session.scalar(
                select(Person).where(
                    Person.user_id == user_id,
                    or_(
                        Person.normalized_name == person_name.lower(),
                        Person.display_name.ilike(f"%{person_name}%"),
                    ),
                    Person.merged_into_id.is_(None),
                )
            )
            if person:
                stmt = stmt.where(MemoryFact.person_id == person.id)

        facts = (await self.session.scalars(stmt.limit(50))).all()
        if not facts:
            return StructuredAnswer(
                answer="I couldn't find any matching facts.",
                sources=[],
                pipeline="inventory:facts",
            )

        lines = [f"• {f.display_label or f.key}: {f.value}" for f in facts[:20]]
        answer = "Here's what I found:\n" + "\n".join(lines)
        sources = [
            SearchResult(
                source_type="fact",
                fact_id=f.id,
                content_preview=f"{f.display_label or f.key}: {f.value}",
                match_type="inventory:fact",
                score=1.0,
            )
            for f in facts[:20]
        ]
        return StructuredAnswer(answer=answer, sources=sources, pipeline="inventory:facts")

    async def _temporal(self, user_id: UUID, query: str) -> StructuredAnswer | None:
        month = _extract_month(query)

        helpers = (
            await self.session.scalars(
                select(Helper)
                .join(MemoryObject, Helper.memory_object_id == MemoryObject.id)
                .where(
                    MemoryObject.user_id == user_id,
                    Helper.status == HelperStatus.accepted,
                    Helper.scheduled_at.is_not(None),
                )
            )
        ).all()

        birthday_facts = (
            await self.session.scalars(
                select(MemoryFact).where(
                    MemoryFact.user_id == user_id,
                    MemoryFact.key.in_(["birthday", "is_birthday"]),
                )
            )
        ).all()

        items: list[tuple[str, SearchResult]] = []

        for helper in helpers:
            if not helper.scheduled_at:
                continue
            scheduled = helper.scheduled_at
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=UTC)
            if month and scheduled.month != month:
                continue
            preview = f"{helper.title}: {helper.value or ''} ({scheduled.date().isoformat()})"
            items.append(
                (
                    preview,
                    SearchResult(
                        memory_id=helper.memory_object_id,
                        content_preview=preview[:200],
                        match_type="temporal:helper",
                        score=1.0,
                    ),
                )
            )

        for fact in birthday_facts:
            preview = f"Birthday — {fact.person or 'someone'}: {fact.value}"
            if month:
                parsed = self._parse_birthday_month(fact.value)
                if parsed and parsed != month:
                    continue
            items.append(
                (
                    preview,
                    SearchResult(
                        source_type="fact",
                        fact_id=fact.id,
                        content_preview=preview[:200],
                        match_type="temporal:birthday",
                        score=0.95,
                    ),
                )
            )

        if not items:
            label = f"month {month}" if month else "that time period"
            return StructuredAnswer(
                answer=f"I couldn't find anything scheduled for {label}.",
                sources=[],
                pipeline="temporal:empty",
            )

        lines = [f"• {item[0]}" for item in items[:15]]
        answer = "Here's what to remember:\n" + "\n".join(lines)
        return StructuredAnswer(
            answer=answer,
            sources=[item[1] for item in items[:15]],
            pipeline="temporal",
        )

    @staticmethod
    def _parse_birthday_month(value: str | None) -> int | None:
        if not value:
            return None
        lower = value.lower()
        for name, num in _MONTH_NAMES.items():
            if name in lower:
                return num
        parts = re.findall(r"\d+", value)
        if len(parts) >= 2:
            month = int(parts[1]) if len(parts[0]) == 4 else int(parts[0])
            if 1 <= month <= 12:
                return month
        return None

    async def _list_accepted_friendships(self, user_id: UUID) -> list[_FriendDisplay]:
        rows = (
            await self.session.scalars(
                select(Friendship).where(
                    Friendship.status == "accepted",
                    or_(Friendship.user_id == user_id, Friendship.friend_id == user_id),
                )
            )
        ).all()
        results: list[_FriendDisplay] = []
        for row in rows:
            other_id = row.friend_id if row.user_id == user_id else row.user_id
            user = await self.session.get(User, other_id)
            if user:
                results.append(
                    _FriendDisplay(
                        id=row.id,
                        display_name=user.display_name or user.username or "Friend",
                    )
                )
        return results

    async def _list_pending_friendships(self, user_id: UUID) -> list[_FriendDisplay]:
        rows = (
            await self.session.scalars(
                select(Friendship).where(
                    Friendship.status == "pending",
                    Friendship.friend_id == user_id,
                )
            )
        ).all()
        results: list[_FriendDisplay] = []
        for row in rows:
            user = await self.session.get(User, row.user_id)
            if user:
                results.append(
                    _FriendDisplay(
                        id=row.id,
                        display_name=user.display_name or user.username or "Friend",
                    )
                )
        return results
