from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_social.service import (
    ConversationMessage,
    ConversationResponse,
    ConversationsService,
    FriendshipResponse,
    FriendsService,
    GroupMemberResponse,
    GroupResponse,
    GroupsService,
    ReactionRequest,
    ReactionResponse,
    ReactionsService,
    ShareRequest,
    ShareResponse,
    SharesService,
    UserSearchResult,
)

router = APIRouter(tags=["social"])


class FriendRequestBody(BaseModel):
    username_or_id: str = Field(min_length=1)


class BlockRequestBody(BaseModel):
    user_id: UUID


class GroupCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class GroupMemberBody(BaseModel):
    user_id: UUID
    role: str = "member"


# --- User discovery ---


@router.get("/users/search", response_model=list[UserSearchResult])
async def search_users(
    q: str = Query(min_length=1),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await FriendsService(db).search_users(q, exclude_user_id=user_id)


# --- Friends ---


@router.get("/friends", response_model=list[FriendshipResponse])
async def list_friends(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await FriendsService(db).list_friends(user_id)


@router.get("/friends/pending", response_model=list[FriendshipResponse])
async def list_pending_friends(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await FriendsService(db).list_pending(user_id)


@router.post("/friends/request", response_model=FriendshipResponse)
async def send_friend_request(
    req: FriendRequestBody,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await FriendsService(db).send_request(user_id, req.username_or_id)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/friends/{friendship_id}/accept", response_model=FriendshipResponse)
async def accept_friend(
    friendship_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await FriendsService(db).accept(user_id, friendship_id)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/friends/{friendship_id}/reject")
async def reject_friend(
    friendship_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await FriendsService(db).reject(user_id, friendship_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/friends/{friend_id}")
async def unfriend(
    friend_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await FriendsService(db).unfriend(user_id, friend_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/friends/block", response_model=FriendshipResponse)
async def block_user(
    req: BlockRequestBody,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await FriendsService(db).block(user_id, req.user_id)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Groups ---


@router.get("/groups", response_model=list[GroupResponse])
async def list_groups(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await GroupsService(db).list_groups(user_id)


@router.post("/groups", response_model=GroupResponse)
async def create_group(
    req: GroupCreateBody,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await GroupsService(db).create(user_id, req.name)
    await db.commit()
    return result


@router.get("/groups/{group_id}/members", response_model=list[GroupMemberResponse])
async def list_group_members(
    group_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await GroupsService(db).list_members(user_id, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/groups/{group_id}/members", response_model=GroupMemberResponse)
async def add_group_member(
    group_id: UUID,
    req: GroupMemberBody,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await GroupsService(db).add_member(user_id, group_id, req.user_id, req.role)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/groups/{group_id}/members/{member_user_id}")
async def remove_group_member(
    group_id: UUID,
    member_user_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await GroupsService(db).remove_member(user_id, group_id, member_user_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/groups/{group_id}/conversation", response_model=ConversationResponse)
async def get_group_conversation(
    group_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await GroupsService(db).get_conversation(user_id, group_id)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Shares ---


@router.post("/memories/{memory_id}/share", response_model=ShareResponse)
async def share_memory(
    memory_id: UUID,
    req: ShareRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        perms = req.permissions.model_dump() if req.permissions else None
        result = await SharesService(db).share_memory(
            user_id,
            memory_id,
            with_user_id=req.with_user_id,
            with_group_id=req.with_group_id,
            conversation_id=req.conversation_id,
            permissions=perms,
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/shares/inbox", response_model=list[ShareResponse])
async def list_shared_with_me(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SharesService(db).list_shared_with_me(user_id)


@router.delete("/shares/{share_id}")
async def revoke_share(
    share_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await SharesService(db).revoke(user_id, share_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Reactions ---


@router.get("/memories/{memory_id}/reactions", response_model=list[ReactionResponse])
async def list_reactions(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ReactionsService(db).list_reactions(user_id, memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/memories/{memory_id}/reactions", response_model=ReactionResponse)
async def add_reaction(
    memory_id: UUID,
    req: ReactionRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await ReactionsService(db).add(user_id, memory_id, req.emoji)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/memories/{memory_id}/reactions/{emoji}")
async def remove_reaction(
    memory_id: UUID,
    emoji: str,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await ReactionsService(db).remove(user_id, memory_id, emoji)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Conversations / DMs ---


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await ConversationsService(db).list_for_user(user_id)


@router.post("/conversations/dm/{friend_id}", response_model=ConversationResponse)
async def get_or_create_dm(
    friend_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await ConversationsService(db).get_or_create_dm(user_id, friend_id)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessage])
async def list_conversation_messages(
    conversation_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ConversationsService(db).list_messages(user_id, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class PostMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


@router.post("/conversations/{conversation_id}/messages", response_model=ConversationMessage)
async def post_conversation_message(
    conversation_id: UUID,
    req: PostMessageBody,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await ConversationsService(db).post_message(user_id, conversation_id, req.text)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
