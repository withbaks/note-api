from datetime import UTC, datetime
import secrets
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import (
    DEFAULT_SHARE_PERMISSIONS,
    AIJob,
    AIState,
    Conversation,
    ConversationParticipant,
    Friendship,
    Group,
    GroupMember,
    Lifecycle,
    MemoryObject,
    MemoryType,
    PublicShareLink,
    Reaction,
    Share,
    User,
    Visibility,
)


class UserSearchResult(BaseModel):
    id: UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}


class FriendshipResponse(BaseModel):
    id: UUID
    user_id: UUID
    friend_id: UUID
    status: str
    created_at: datetime
    friend: UserSearchResult | None = None

    model_config = {"from_attributes": True}


class GroupResponse(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberResponse(BaseModel):
    id: UUID
    group_id: UUID
    user_id: UUID
    role: str
    created_at: datetime
    user: UserSearchResult | None = None

    model_config = {"from_attributes": True}


class SharePermissions(BaseModel):
    view: bool = True
    react: bool = True
    forward: bool = True
    ask_ai: bool = True


class ShareRequest(BaseModel):
    with_user_id: UUID | None = None
    with_group_id: UUID | None = None
    conversation_id: UUID | None = None
    permissions: SharePermissions | None = None


class ShareResponse(BaseModel):
    id: UUID
    memory_object_id: UUID
    shared_with_id: UUID | None
    shared_with_group_id: UUID | None
    conversation_id: UUID | None
    permissions: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ReactionResponse(BaseModel):
    id: UUID
    memory_object_id: UUID
    user_id: UUID
    emoji: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: UUID
    kind: str
    title: str | None
    group_id: UUID | None
    created_at: datetime
    participant_ids: list[UUID] = []

    model_config = {"from_attributes": True}


class ConversationMessage(BaseModel):
    share_id: UUID
    memory_object_id: UUID
    shared_by_user_id: UUID
    content_preview: str | None
    created_at: datetime


class PublicLinkResponse(BaseModel):
    token: str
    url: str
    memory_object_id: UUID
    created_at: datetime


class PublicNoteResponse(BaseModel):
    id: UUID
    type: MemoryType
    content_text: str | None
    structured_title: str | None
    structured_value: str | None
    media_uri: str | None
    media_type: str | None
    created_at: datetime
    owner_display_name: str | None = None

    model_config = {"from_attributes": True}


class PostMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


def _user_public(user: User) -> UserSearchResult:
    return UserSearchResult(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
    )


class FriendsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search_users(self, q: str, exclude_user_id: UUID | None = None, limit: int = 20) -> list[UserSearchResult]:
        pattern = f"%{q.strip()}%"
        stmt = (
            select(User)
            .where(
                User.deleted_at.is_(None),
                or_(User.username.ilike(pattern), User.display_name.ilike(pattern)),
            )
            .limit(limit)
        )
        if exclude_user_id:
            stmt = stmt.where(User.id != exclude_user_id)
        users = (await self.session.scalars(stmt)).all()
        return [_user_public(u) for u in users]

    async def send_request(self, user_id: UUID, friend_username_or_id: str) -> FriendshipResponse:
        friend = await self._resolve_user(friend_username_or_id)
        if friend.id == user_id:
            raise ValueError("Cannot friend yourself")

        existing = await self.session.scalar(
            select(Friendship).where(
                or_(
                    and_(Friendship.user_id == user_id, Friendship.friend_id == friend.id),
                    and_(Friendship.user_id == friend.id, Friendship.friend_id == user_id),
                )
            )
        )
        if existing:
            if existing.status == "blocked":
                raise ValueError("Friendship blocked")
            if existing.status == "accepted":
                raise ValueError("Already friends")
            return await self._friendship_response(existing, viewer_id=user_id)

        friendship = Friendship(user_id=user_id, friend_id=friend.id, status="pending")
        self.session.add(friendship)
        await self.session.flush()
        return await self._friendship_response(friendship, viewer_id=user_id)

    async def list_friends(self, user_id: UUID) -> list[FriendshipResponse]:
        stmt = select(Friendship).where(
            Friendship.status == "accepted",
            or_(Friendship.user_id == user_id, Friendship.friend_id == user_id),
        )
        rows = (await self.session.scalars(stmt)).all()
        return [await self._friendship_response(r, viewer_id=user_id) for r in rows]

    async def list_pending(self, user_id: UUID) -> list[FriendshipResponse]:
        stmt = select(Friendship).where(
            Friendship.status == "pending",
            Friendship.friend_id == user_id,
        )
        rows = (await self.session.scalars(stmt)).all()
        return [await self._friendship_response(r, viewer_id=user_id) for r in rows]

    async def accept(self, user_id: UUID, friendship_id: UUID) -> FriendshipResponse:
        friendship = await self._get_incoming_pending(user_id, friendship_id)
        friendship.status = "accepted"
        await self.session.flush()
        return await self._friendship_response(friendship, viewer_id=user_id)

    async def reject(self, user_id: UUID, friendship_id: UUID) -> None:
        friendship = await self._get_incoming_pending(user_id, friendship_id)
        await self.session.delete(friendship)

    async def unfriend(self, user_id: UUID, friend_id: UUID) -> None:
        friendship = await self.session.scalar(
            select(Friendship).where(
                Friendship.status == "accepted",
                or_(
                    and_(Friendship.user_id == user_id, Friendship.friend_id == friend_id),
                    and_(Friendship.user_id == friend_id, Friendship.friend_id == user_id),
                ),
            )
        )
        if not friendship:
            raise ValueError("Friendship not found")
        await self.session.delete(friendship)

    async def block(self, user_id: UUID, friend_id: UUID) -> FriendshipResponse:
        if friend_id == user_id:
            raise ValueError("Cannot block yourself")
        friendship = await self.session.scalar(
            select(Friendship).where(
                or_(
                    and_(Friendship.user_id == user_id, Friendship.friend_id == friend_id),
                    and_(Friendship.user_id == friend_id, Friendship.friend_id == user_id),
                )
            )
        )
        if friendship:
            friendship.user_id = user_id
            friendship.friend_id = friend_id
            friendship.status = "blocked"
        else:
            friendship = Friendship(user_id=user_id, friend_id=friend_id, status="blocked")
            self.session.add(friendship)
        await self.session.flush()
        return await self._friendship_response(friendship, viewer_id=user_id)

    async def _resolve_user(self, username_or_id: str) -> User:
        try:
            uid = UUID(username_or_id)
            user = await self.session.get(User, uid)
        except ValueError:
            user = await self.session.scalar(
                select(User).where(User.username == username_or_id, User.deleted_at.is_(None))
            )
        if not user or user.deleted_at is not None:
            raise ValueError("User not found")
        return user

    async def _get_incoming_pending(self, user_id: UUID, friendship_id: UUID) -> Friendship:
        friendship = await self.session.get(Friendship, friendship_id)
        if not friendship or friendship.friend_id != user_id or friendship.status != "pending":
            raise ValueError("Friend request not found")
        return friendship

    async def _friendship_response(self, friendship: Friendship, viewer_id: UUID) -> FriendshipResponse:
        other_id = friendship.friend_id if friendship.user_id == viewer_id else friendship.user_id
        other = await self.session.get(User, other_id)
        return FriendshipResponse(
            id=friendship.id,
            user_id=friendship.user_id,
            friend_id=friendship.friend_id,
            status=friendship.status,
            created_at=friendship.created_at,
            friend=_user_public(other) if other else None,
        )


class GroupsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user_id: UUID, name: str) -> GroupResponse:
        group = Group(name=name.strip(), owner_id=user_id)
        self.session.add(group)
        await self.session.flush()
        self.session.add(GroupMember(group_id=group.id, user_id=user_id, role="owner"))

        # Every group gets a shared conversation thread for chat memories.
        conv = Conversation(id=uuid4(), kind="group", title=group.name, group_id=group.id)
        self.session.add(conv)
        await self.session.flush()
        self.session.add(ConversationParticipant(conversation_id=conv.id, user_id=user_id))
        await self.session.flush()
        return GroupResponse.model_validate(group)

    async def get_conversation(self, user_id: UUID, group_id: UUID) -> ConversationResponse:
        await self._require_member(user_id, group_id)
        conv = await self.session.scalar(
            select(Conversation).where(Conversation.group_id == group_id, Conversation.kind == "group")
        )
        if not conv:
            # Backfill for groups created before group-chat wiring
            group = await self.session.get(Group, group_id)
            conv = Conversation(
                id=uuid4(),
                kind="group",
                title=group.name if group else None,
                group_id=group_id,
            )
            self.session.add(conv)
            await self.session.flush()
            members = (
                await self.session.scalars(select(GroupMember).where(GroupMember.group_id == group_id))
            ).all()
            for m in members:
                self.session.add(
                    ConversationParticipant(conversation_id=conv.id, user_id=m.user_id)
                )
            await self.session.flush()
        return await ConversationsService(self.session)._to_response(conv)

    async def list_groups(self, user_id: UUID) -> list[GroupResponse]:
        stmt = (
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(GroupMember.user_id == user_id)
            .order_by(Group.created_at.desc())
        )
        groups = (await self.session.scalars(stmt)).all()
        return [GroupResponse.model_validate(g) for g in groups]

    async def add_member(self, user_id: UUID, group_id: UUID, member_user_id: UUID, role: str = "member") -> GroupMemberResponse:
        group = await self._require_owner_or_admin(user_id, group_id)
        existing = await self.session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.user_id == member_user_id,
            )
        )
        if existing:
            raise ValueError("Already a member")
        member = GroupMember(group_id=group.id, user_id=member_user_id, role=role)
        self.session.add(member)

        # Keep group chat participants in sync
        conv = await self.session.scalar(
            select(Conversation).where(Conversation.group_id == group_id, Conversation.kind == "group")
        )
        if conv:
            already = await self.session.scalar(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conv.id,
                    ConversationParticipant.user_id == member_user_id,
                )
            )
            if not already:
                self.session.add(
                    ConversationParticipant(conversation_id=conv.id, user_id=member_user_id)
                )

        await self.session.flush()
        user = await self.session.get(User, member_user_id)
        return GroupMemberResponse(
            id=member.id,
            group_id=member.group_id,
            user_id=member.user_id,
            role=member.role,
            created_at=member.created_at,
            user=_user_public(user) if user else None,
        )

    async def remove_member(self, user_id: UUID, group_id: UUID, member_user_id: UUID) -> None:
        group = await self.session.get(Group, group_id)
        if not group:
            raise ValueError("Group not found")
        if group.owner_id != user_id and user_id != member_user_id:
            raise ValueError("Not authorized")
        if member_user_id == group.owner_id:
            raise ValueError("Cannot remove group owner")
        member = await self.session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == member_user_id,
            )
        )
        if not member:
            raise ValueError("Member not found")
        await self.session.delete(member)

    async def list_members(self, user_id: UUID, group_id: UUID) -> list[GroupMemberResponse]:
        await self._require_member(user_id, group_id)
        stmt = select(GroupMember).where(GroupMember.group_id == group_id)
        members = (await self.session.scalars(stmt)).all()
        results = []
        for m in members:
            user = await self.session.get(User, m.user_id)
            results.append(
                GroupMemberResponse(
                    id=m.id,
                    group_id=m.group_id,
                    user_id=m.user_id,
                    role=m.role,
                    created_at=m.created_at,
                    user=_user_public(user) if user else None,
                )
            )
        return results

    async def _require_member(self, user_id: UUID, group_id: UUID) -> Group:
        group = await self.session.get(Group, group_id)
        if not group:
            raise ValueError("Group not found")
        member = await self.session.scalar(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        )
        if not member:
            raise ValueError("Not a group member")
        return group

    async def _require_owner_or_admin(self, user_id: UUID, group_id: UUID) -> Group:
        group = await self.session.get(Group, group_id)
        if not group:
            raise ValueError("Group not found")
        member = await self.session.scalar(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        )
        if not member or (group.owner_id != user_id and member.role not in ("owner", "admin")):
            raise ValueError("Not authorized")
        return group


class SharesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def share_memory(
        self,
        user_id: UUID,
        memory_id: UUID,
        with_user_id: UUID | None = None,
        with_group_id: UUID | None = None,
        conversation_id: UUID | None = None,
        permissions: dict | None = None,
    ) -> ShareResponse:
        if bool(with_user_id) == bool(with_group_id):
            raise ValueError("Provide exactly one of with_user_id or with_group_id")

        mem = await self.session.get(MemoryObject, memory_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Memory not found")

        perms = permissions or dict(DEFAULT_SHARE_PERMISSIONS)
        share = Share(
            memory_object_id=memory_id,
            shared_with_id=with_user_id,
            shared_with_group_id=with_group_id,
            conversation_id=conversation_id,
            permissions=perms,
        )
        self.session.add(share)

        if with_group_id:
            mem.visibility = Visibility.group
        else:
            mem.visibility = Visibility.shared

        if with_user_id and not conversation_id:
            conv = await ConversationsService(self.session).get_or_create_dm(user_id, with_user_id)
            share.conversation_id = conv.id

        await self.session.flush()
        return ShareResponse.model_validate(share)

    async def list_shared_with_me(self, user_id: UUID) -> list[ShareResponse]:
        group_ids = list(
            await self.session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
        )
        conditions = [Share.shared_with_id == user_id]
        if group_ids:
            conditions.append(Share.shared_with_group_id.in_(group_ids))
        stmt = select(Share).where(or_(*conditions)).order_by(Share.created_at.desc())
        shares = (await self.session.scalars(stmt)).all()
        return [ShareResponse.model_validate(s) for s in shares]

    async def revoke(self, user_id: UUID, share_id: UUID) -> None:
        share = await self.session.get(Share, share_id)
        if not share:
            raise ValueError("Share not found")
        mem = await self.session.get(MemoryObject, share.memory_object_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Not authorized")
        await self.session.delete(share)


class ReactionsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, user_id: UUID, memory_id: UUID, emoji: str) -> ReactionResponse:
        await self._assert_can_react(user_id, memory_id)
        existing = await self.session.scalar(
            select(Reaction).where(
                Reaction.memory_object_id == memory_id,
                Reaction.user_id == user_id,
                Reaction.emoji == emoji,
            )
        )
        if existing:
            return ReactionResponse.model_validate(existing)
        reaction = Reaction(memory_object_id=memory_id, user_id=user_id, emoji=emoji)
        self.session.add(reaction)
        await self.session.flush()
        return ReactionResponse.model_validate(reaction)

    async def remove(self, user_id: UUID, memory_id: UUID, emoji: str) -> None:
        reaction = await self.session.scalar(
            select(Reaction).where(
                Reaction.memory_object_id == memory_id,
                Reaction.user_id == user_id,
                Reaction.emoji == emoji,
            )
        )
        if not reaction:
            raise ValueError("Reaction not found")
        await self.session.delete(reaction)

    async def list_reactions(self, user_id: UUID, memory_id: UUID) -> list[ReactionResponse]:
        await self._assert_can_react(user_id, memory_id)
        stmt = select(Reaction).where(Reaction.memory_object_id == memory_id).order_by(Reaction.created_at)
        reactions = (await self.session.scalars(stmt)).all()
        return [ReactionResponse.model_validate(r) for r in reactions]

    async def _assert_can_react(self, user_id: UUID, memory_id: UUID) -> MemoryObject:
        mem = await self.session.get(MemoryObject, memory_id)
        if not mem:
            raise ValueError("Memory not found")
        if mem.user_id == user_id:
            return mem
        group_ids = list(
            await self.session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
        )
        conditions = [Share.shared_with_id == user_id]
        if group_ids:
            conditions.append(Share.shared_with_group_id.in_(group_ids))
        share = await self.session.scalar(
            select(Share).where(Share.memory_object_id == memory_id, or_(*conditions))
        )
        if not share:
            raise ValueError("Not authorized")
        perms = share.permissions or {}
        if not perms.get("react", True):
            raise ValueError("React not permitted")
        return mem


class ConversationsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_dms(self, user_id: UUID) -> list[ConversationResponse]:
        stmt = (
            select(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .where(
                ConversationParticipant.user_id == user_id,
                Conversation.kind == "dm",
            )
            .order_by(Conversation.created_at.desc())
        )
        convs = (await self.session.scalars(stmt)).unique().all()
        return [await self._to_response(c) for c in convs]

    async def get_or_create_dm(self, user_id: UUID, friend_id: UUID) -> ConversationResponse:
        if user_id == friend_id:
            raise ValueError("Cannot DM yourself")

        # Find existing DM with exactly these two participants
        my_conv_ids = (
            await self.session.scalars(
                select(ConversationParticipant.conversation_id).where(
                    ConversationParticipant.user_id == user_id
                )
            )
        ).all()
        if my_conv_ids:
            stmt = (
                select(Conversation)
                .join(ConversationParticipant)
                .where(
                    Conversation.id.in_(my_conv_ids),
                    Conversation.kind == "dm",
                    ConversationParticipant.user_id == friend_id,
                )
            )
            existing = await self.session.scalar(stmt)
            if existing:
                return await self._to_response(existing)

        conv = Conversation(id=uuid4(), kind="dm")
        self.session.add(conv)
        await self.session.flush()
        self.session.add(ConversationParticipant(conversation_id=conv.id, user_id=user_id))
        self.session.add(ConversationParticipant(conversation_id=conv.id, user_id=friend_id))
        await self.session.flush()
        return await self._to_response(conv)

    async def list_messages(self, user_id: UUID, conversation_id: UUID) -> list[ConversationMessage]:
        participant = await self.session.scalar(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        if not participant:
            raise ValueError("Conversation not found")

        stmt = (
            select(Share, MemoryObject)
            .join(MemoryObject, Share.memory_object_id == MemoryObject.id)
            .where(Share.conversation_id == conversation_id)
            .order_by(Share.created_at.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        messages = []
        for share, mem in rows:
            preview = mem.content_text or (
                f"{mem.structured_title}: {mem.structured_value}" if mem.structured_title else None
            )
            messages.append(
                ConversationMessage(
                    share_id=share.id,
                    memory_object_id=mem.id,
                    shared_by_user_id=mem.user_id,
                    content_preview=preview[:200] if preview else None,
                    created_at=share.created_at,
                )
            )
        return messages

    async def post_message(
        self, user_id: UUID, conversation_id: UUID, text: str
    ) -> ConversationMessage:
        """Post a chat message: creates a memory object and shares it into the conversation."""
        conv = await self.session.get(Conversation, conversation_id)
        if not conv:
            raise ValueError("Conversation not found")

        participant = await self.session.scalar(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        if not participant:
            raise ValueError("Not a conversation participant")

        mem_id = uuid4()
        mem = MemoryObject(
            id=mem_id,
            user_id=user_id,
            type=MemoryType.text,
            origin="group_chat" if conv.kind == "group" else "dm",
            content_text=text.strip(),
            visibility=Visibility.group if conv.kind == "group" else Visibility.shared,
            ai_state=AIState.captured,
            hlc=f"0:{int(datetime.now(UTC).timestamp() * 1000)}:{user_id}",
        )
        self.session.add(mem)
        self.session.add(AIJob(memory_object_id=mem_id, state=AIState.captured))

        share = Share(
            memory_object_id=mem_id,
            shared_with_id=None,
            shared_with_group_id=conv.group_id if conv.kind == "group" else None,
            conversation_id=conversation_id,
            permissions=dict(DEFAULT_SHARE_PERMISSIONS),
        )
        # For DMs, set shared_with to the other participant
        if conv.kind == "dm":
            others = (
                await self.session.scalars(
                    select(ConversationParticipant.user_id).where(
                        ConversationParticipant.conversation_id == conversation_id,
                        ConversationParticipant.user_id != user_id,
                    )
                )
            ).all()
            if others:
                share.shared_with_id = others[0]

        self.session.add(share)
        await self.session.flush()

        # Best-effort AI enqueue
        try:
            from note_queue import enqueue_ai_job, get_redis_pool

            pool = await get_redis_pool()
            await enqueue_ai_job(pool, str(mem_id))
        except Exception:
            pass

        return ConversationMessage(
            share_id=share.id,
            memory_object_id=mem.id,
            shared_by_user_id=user_id,
            content_preview=text.strip()[:200],
            created_at=share.created_at or datetime.now(UTC),
        )

    async def list_for_user(self, user_id: UUID) -> list[ConversationResponse]:
        """List DMs and group conversations the user participates in."""
        stmt = (
            select(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .where(ConversationParticipant.user_id == user_id)
            .order_by(Conversation.created_at.desc())
        )
        convs = (await self.session.scalars(stmt)).unique().all()
        return [await self._to_response(c) for c in convs]

    async def _to_response(self, conv: Conversation) -> ConversationResponse:
        participants = (
            await self.session.scalars(
                select(ConversationParticipant.user_id).where(
                    ConversationParticipant.conversation_id == conv.id
                )
            )
        ).all()
        return ConversationResponse(
            id=conv.id,
            kind=conv.kind,
            title=conv.title,
            group_id=conv.group_id,
            created_at=conv.created_at,
            participant_ids=list(participants),
        )


class PublicShareService:
    def __init__(self, session: AsyncSession, web_base_url: str) -> None:
        self.session = session
        self.web_base_url = web_base_url.rstrip("/")

    async def create_or_get_link(self, user_id: UUID, memory_id: UUID) -> PublicLinkResponse:
        mem = await self.session.get(MemoryObject, memory_id)
        if not mem or mem.user_id != user_id or mem.lifecycle != Lifecycle.active:
            raise ValueError("Memory not found")
        if mem.deleted_at is not None:
            raise ValueError("Memory not found")

        link = await self.session.scalar(
            select(PublicShareLink).where(PublicShareLink.memory_object_id == memory_id)
        )
        if link and link.revoked_at is not None:
            link = None

        if not link:
            link = PublicShareLink(
                memory_object_id=memory_id,
                token=secrets.token_urlsafe(24),
                created_by_user_id=user_id,
            )
            self.session.add(link)

        mem.visibility = Visibility.public
        link.revoked_at = None
        await self.session.flush()

        return PublicLinkResponse(
            token=link.token,
            url=f"{self.web_base_url}/m/{link.token}",
            memory_object_id=memory_id,
            created_at=link.created_at,
        )

    async def revoke_link(self, user_id: UUID, memory_id: UUID) -> None:
        mem = await self.session.get(MemoryObject, memory_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Memory not found")
        link = await self.session.scalar(
            select(PublicShareLink).where(PublicShareLink.memory_object_id == memory_id)
        )
        if not link:
            raise ValueError("Public link not found")
        link.revoked_at = datetime.now(UTC)
        if mem.visibility == Visibility.public:
            mem.visibility = Visibility.private
        await self.session.flush()

    async def get_public_note(self, token: str) -> PublicNoteResponse:
        link = await self.session.scalar(
            select(PublicShareLink).where(PublicShareLink.token == token)
        )
        if not link or link.revoked_at is not None:
            raise ValueError("Note not found")

        mem = await self.session.get(MemoryObject, link.memory_object_id)
        if not mem or mem.lifecycle != Lifecycle.active or mem.deleted_at is not None:
            raise ValueError("Note not found")

        owner = await self.session.get(User, mem.user_id)
        media_uri = mem.media_uri if mem.media_uri and mem.media_uri.startswith("http") else None

        return PublicNoteResponse(
            id=mem.id,
            type=mem.type,
            content_text=mem.content_text,
            structured_title=mem.structured_title,
            structured_value=mem.structured_value,
            media_uri=media_uri,
            media_type=mem.media_type if media_uri else None,
            created_at=mem.created_at,
            owner_display_name=owner.display_name if owner else None,
        )
