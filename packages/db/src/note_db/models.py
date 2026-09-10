import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship
from sqlalchemy import event
from pgvector.sqlalchemy import Vector

from note_db.session import Base


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Visibility(str, enum.Enum):
    private = "private"
    shared = "shared"
    group = "group"
    public = "public"


class Lifecycle(str, enum.Enum):
    active = "active"
    hidden = "hidden"
    archived = "archived"
    deleted = "deleted"


class MemoryType(str, enum.Enum):
    text = "text"
    structured = "structured"
    image = "image"
    bookmark = "bookmark"


class AIState(str, enum.Enum):
    captured = "captured"
    processing = "processing"
    understood = "understood"
    organized = "organized"
    indexed = "indexed"
    failed = "failed"


class HelperSource(str, enum.Enum):
    heuristic = "heuristic"
    llm = "llm"
    user = "user"


class HelperStatus(str, enum.Enum):
    suggested = "suggested"
    accepted = "accepted"
    dismissed = "dismissed"


class CategorySource(str, enum.Enum):
    user = "user"
    ai = "ai"


class AdminRole(str, enum.Enum):
    viewer = "viewer"
    support = "support"
    admin = "admin"


class MemoryPersonRole(str, enum.Enum):
    subject = "subject"
    mentioned = "mentioned"
    companion = "companion"


# System semantic types (not user folders)
SEMANTIC_TYPES = frozenset(
    {
        "person_meta",
        "contact",
        "measurement",
        "health",
        "education",
        "event",
        "place",
        "product",
        "preference",
        "reminder",
        "goal",
    }
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    apple_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_use_notes_context: Mapped[bool] = mapped_column(Boolean, default=True)
    helper_auto_accept: Mapped[bool] = mapped_column(Boolean, default=False)
    helper_trust: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_monthly_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_role: Mapped[AdminRole | None] = mapped_column(Enum(AdminRole), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    devices: Mapped[list["Device"]] = orm_relationship(back_populates="user")
    sessions: Mapped[list["Session"]] = orm_relationship(back_populates="user")
    memory_objects: Mapped[list["MemoryObject"]] = orm_relationship(back_populates="user")
    people: Mapped[list["Person"]] = orm_relationship(
        "Person",
        back_populates="user",
        foreign_keys="Person.user_id",
    )


class Person(Base):
    """First-class people records (self, mum, Joshua, …)."""

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128), index=True)
    relationship: Mapped[str | None] = mapped_column(String(64), nullable=True)  # self|mother|friend|…
    aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), default="inferred")
    contact_link_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = orm_relationship(
        "User",
        back_populates="people",
        foreign_keys="Person.user_id",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "normalized_name", name="uq_people_user_normalized_name"),
        Index("ix_people_user_relationship", "user_id", "relationship"),
    )


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="Unknown Device")
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = orm_relationship(back_populates="devices")
    sessions: Mapped[list["Session"]] = orm_relationship(back_populates="device")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = orm_relationship(back_populates="sessions")
    device: Mapped["Device"] = orm_relationship(back_populates="sessions")


class MemoryObject(Base):
    __tablename__ = "memory_objects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    type: Mapped[MemoryType] = mapped_column(Enum(MemoryType), default=MemoryType.text)
    origin: Mapped[str] = mapped_column(String(64), default="capture")
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    structured_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    visibility: Mapped[Visibility] = mapped_column(Enum(Visibility), default=Visibility.private)
    lifecycle: Mapped[Lifecycle] = mapped_column(Enum(Lifecycle), default=Lifecycle.active)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_state: Mapped[AIState] = mapped_column(Enum(AIState), default=AIState.captured)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = orm_relationship(back_populates="memory_objects")
    versions: Mapped[list["MemoryVersion"]] = orm_relationship(back_populates="memory_object")
    understandings: Mapped[list["Understanding"]] = orm_relationship(back_populates="memory_object")
    helpers: Mapped[list["Helper"]] = orm_relationship(back_populates="memory_object")
    categories: Mapped[list["MemoryCategory"]] = orm_relationship(back_populates="memory_object")
    entities: Mapped[list["Entity"]] = orm_relationship(back_populates="memory_object")
    search_documents: Mapped[list["SearchDocument"]] = orm_relationship(back_populates="memory_object")
    ai_jobs: Mapped[list["AIJob"]] = orm_relationship(back_populates="memory_object")

    __table_args__ = (
        Index("ix_memory_objects_user_lifecycle", "user_id", "lifecycle"),
        Index("ix_memory_objects_user_updated", "user_id", "updated_at"),
    )


class MemoryVersion(Base):
    __tablename__ = "memory_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    hlc: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="versions")


class Understanding(Base):
    __tablename__ = "understandings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    topics: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    connections: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="understandings")


class Helper(Base):
    __tablename__ = "helpers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="knowledge")
    proposed_fact_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    helper_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    person: Mapped[str | None] = mapped_column(String(128), nullable=True)  # legacy; prefer person_id
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True, index=True
    )
    semantic_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[HelperSource] = mapped_column(Enum(HelperSource), default=HelperSource.heuristic)
    status: Mapped[HelperStatus] = mapped_column(Enum(HelperStatus), default=HelperStatus.suggested)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="helpers")
    tool_runs: Mapped[list["ToolRun"]] = orm_relationship(back_populates="helper")


@event.listens_for(Helper, "before_insert")
@event.listens_for(Helper, "before_update")
def _helper_apply_defaults(_mapper, _connection, target: Helper) -> None:
    if not target.title:
        key = target.key or "suggestion"
        target.title = key.replace("is_", "").replace("_", " ").title()
    if not target.kind:
        target.kind = "knowledge"


class ToolRun(Base):
    __tablename__ = "tool_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    helper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("helpers.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    helper: Mapped["Helper"] = orm_relationship(back_populates="tool_runs")


class MemoryFact(Base):
    __tablename__ = "memory_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(256))
    display_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    fact_type: Mapped[str] = mapped_column(String(32), default="attribute")
    value: Mapped[str] = mapped_column(Text)
    person: Mapped[str | None] = mapped_column(String(128), nullable=True)  # legacy
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True, index=True
    )
    semantic_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_override: Mapped[bool] = mapped_column(Boolean, default=False)
    source_memory_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    last_reaffirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_volatility: Mapped[str] = mapped_column(String(32), default="slow")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "key", "person_id", name="uq_memory_facts_user_key_person_id"),
    )


class MemoryPerson(Base):
    """Link memories to people (subject / mentioned / companion)."""

    __tablename__ = "memory_people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), default=MemoryPersonRole.mentioned.value)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("memory_object_id", "person_id", "role", name="uq_memory_people_triple"),
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[CategorySource] = mapped_column(Enum(CategorySource), default=CategorySource.user)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memory_links: Mapped[list["MemoryCategory"]] = orm_relationship(back_populates="category")

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_categories_user_name"),)


class MemoryCategory(Base):
    __tablename__ = "memory_categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), index=True
    )
    source: Mapped[CategorySource] = mapped_column(Enum(CategorySource), default=CategorySource.user)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="categories")
    category: Mapped["Category"] = orm_relationship(back_populates="memory_links")

    __table_args__ = (
        UniqueConstraint("memory_object_id", "category_id", name="uq_memory_categories_pair"),
    )


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_entities: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    first_memory_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_memory_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memory_links: Mapped[list["MemoryThread"]] = orm_relationship(back_populates="thread")


class MemoryThread(Base):
    __tablename__ = "memory_threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id"), index=True
    )
    hlc: Mapped[str] = mapped_column(String(128), default="0:0:server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    thread: Mapped["Thread"] = orm_relationship(back_populates="memory_links")

    __table_args__ = (
        UniqueConstraint("memory_object_id", "thread_id", name="uq_memory_threads_pair"),
    )


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    state: Mapped[AIState] = mapped_column(Enum(AIState), default=AIState.captured)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_completed_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_result_cache: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="ai_jobs")


class FactHistory(Base):
    __tablename__ = "fact_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_facts.id"), index=True
    )
    value: Mapped[str] = mapped_column(Text)
    source_memory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(32), default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    memory_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), nullable=True, index=True
    )
    operation: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(512))
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="entities")


class SearchDocument(Base):
    __tablename__ = "search_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    memory_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True, nullable=True
    )
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)
    doc_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memory_object: Mapped["MemoryObject"] = orm_relationship(back_populates="search_documents")

    __table_args__ = (
        Index("ix_search_documents_user_memory", "user_id", "memory_object_id"),
    )


class SyncMutation(Base):
    __tablename__ = "sync_mutations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    operation: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    hlc: Mapped[str] = mapped_column(String(128))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("id", name="uq_sync_mutations_id"),)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    cursor: Mapped[str] = mapped_column(String(128), default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_sync_cursors_user_device"),)


DEFAULT_SHARE_PERMISSIONS: dict = {
    "view": True,
    "react": True,
    "forward": True,
    "ask_ai": True,
}


class Friendship(Base):
    __tablename__ = "friendships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    friend_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|accepted|blocked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "friend_id", name="uq_friendships_user_friend"),
    )


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["GroupMember"]] = orm_relationship(back_populates="group")


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped["Group"] = orm_relationship(back_populates="members")

    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(32), default="dm")  # dm|group
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    participants: Mapped[list["ConversationParticipant"]] = orm_relationship(back_populates="conversation")


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = orm_relationship(back_populates="participants")

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participants_pair"),
    )


class Share(Base):
    __tablename__ = "shares"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    shared_with_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    shared_with_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True, index=True
    )
    permissions: Mapped[dict] = mapped_column(
        JSONB, default=lambda: dict(DEFAULT_SHARE_PERMISSIONS)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicShareLink(Base):
    __tablename__ = "public_share_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True, unique=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reaction(Base):
    __tablename__ = "reactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    memory_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    emoji: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("memory_object_id", "user_id", "emoji", name="uq_reactions_memory_user_emoji"),
    )


class LinkPreviewCache(Base):
    __tablename__ = "link_preview_cache"

    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    preview: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestItem(Base):
    __tablename__ = "ingest_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    memory_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_objects.id"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|processed|failed
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushToken(Base):
    __tablename__ = "push_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(128))
    token: Mapped[str] = mapped_column(String(512))
    platform: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_push_tokens_user_device"),)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
