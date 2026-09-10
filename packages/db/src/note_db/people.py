"""Resolve / create first-class Person records for a user."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Person, User

RELATIONSHIP_ALIASES: dict[str, tuple[str, list[str]]] = {
    "mother": ("Mum", ["mum", "mom", "mother"]),
    "father": ("Dad", ["dad", "father"]),
    "brother": ("Brother", ["brother"]),
    "sister": ("Sister", ["sister"]),
    "grandma": ("Grandma", ["grandma", "grandmother"]),
    "grandpa": ("Grandpa", ["grandpa", "grandfather"]),
}


def normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


class PeopleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_self(self, user: User) -> Person:
        existing = await self.session.scalar(
            select(Person).where(Person.user_id == user.id, Person.relationship == "self")
        )
        if existing:
            return existing
        display = user.display_name or user.username or "You"
        person = Person(
            user_id=user.id,
            display_name=display,
            normalized_name="self",
            relationship="self",
            aliases=["me", "i", "myself", "self"],
        )
        self.session.add(person)
        await self.session.flush()
        return person

    async def resolve(
        self,
        user_id: UUID,
        *,
        display_name: str | None = None,
        relationship: str | None = None,
        subject_self: bool = False,
        user: User | None = None,
    ) -> Person:
        if subject_self or (relationship or "").lower() == "self":
            if user is None:
                user = await self.session.get(User, user_id)
            if not user:
                raise ValueError("User not found")
            return await self.ensure_self(user)

        rel = (relationship or "").lower().strip() or None
        if rel and rel in RELATIONSHIP_ALIASES:
            display, aliases = RELATIONSHIP_ALIASES[rel]
            by_rel = await self.session.scalar(
                select(Person).where(Person.user_id == user_id, Person.relationship == rel)
            )
            if by_rel:
                return by_rel
            person = Person(
                user_id=user_id,
                display_name=display_name or display,
                normalized_name=normalize_name(display_name or display),
                relationship=rel,
                aliases=aliases,
            )
            self.session.add(person)
            await self.session.flush()
            return person

        if not display_name:
            if user is None:
                user = await self.session.get(User, user_id)
            assert user
            return await self.ensure_self(user)

        norm = normalize_name(display_name)
        match = await self.session.scalar(
            select(Person).where(
                Person.user_id == user_id,
                Person.normalized_name == norm,
                Person.merged_into_id.is_(None),
            )
        )
        if match:
            return match

        people = (
            await self.session.scalars(
                select(Person).where(Person.user_id == user_id, Person.merged_into_id.is_(None))
            )
        ).all()
        for p in people:
            aliases = [a.lower() for a in (p.aliases or [])]
            if norm in aliases or norm == p.normalized_name:
                return p
            if p.relationship and p.relationship in RELATIONSHIP_ALIASES:
                _, rel_aliases = RELATIONSHIP_ALIASES[p.relationship]
                if norm in rel_aliases:
                    return p

        inferred_rel = None
        for r, (_, aliases) in RELATIONSHIP_ALIASES.items():
            if norm in aliases:
                inferred_rel = r
                break

        if inferred_rel:
            by_rel = await self.session.scalar(
                select(Person).where(Person.user_id == user_id, Person.relationship == inferred_rel)
            )
            if by_rel:
                return by_rel

        display = display_name.strip()
        if inferred_rel:
            display = RELATIONSHIP_ALIASES[inferred_rel][0]
        elif display:
            display = display[0].upper() + display[1:]

        person = Person(
            user_id=user_id,
            display_name=display or display_name,
            normalized_name=normalize_name(display or display_name),
            relationship=inferred_rel,
            aliases=RELATIONSHIP_ALIASES[inferred_rel][1] if inferred_rel else None,
        )
        self.session.add(person)
        await self.session.flush()
        return person
