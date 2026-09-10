from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Helper, ToolRun
from note_tools.registry import ToolRunStatus


class ToolRunResponse(BaseModel):
    id: UUID
    helper_id: UUID
    tool_name: str
    status: str
    result: dict | None = None


class ToolsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self, user_id: UUID, helper_id: UUID, tool_name: str, result: dict | None = None
    ) -> ToolRunResponse:
        helper = await self.session.get(Helper, helper_id)
        if not helper:
            raise ValueError("Helper not found")

        status = ToolRunStatus.succeeded if result else ToolRunStatus.pending
        run = ToolRun(
            helper_id=helper_id,
            user_id=user_id,
            tool_name=tool_name,
            status=status.value,
            result=result,
        )
        self.session.add(run)
        await self.session.flush()
        return ToolRunResponse(
            id=run.id,
            helper_id=helper_id,
            tool_name=tool_name,
            status=run.status,
            result=run.result,
        )

    async def complete_run(self, run_id: UUID, status: ToolRunStatus, result: dict | None = None) -> None:
        run = await self.session.get(ToolRun, run_id)
        if not run:
            raise ValueError("Tool run not found")
        run.status = status.value
        run.result = result

    async def list_for_helper(self, helper_id: UUID) -> list[ToolRunResponse]:
        rows = (
            await self.session.scalars(select(ToolRun).where(ToolRun.helper_id == helper_id))
        ).all()
        return [
            ToolRunResponse(
                id=r.id,
                helper_id=r.helper_id,
                tool_name=r.tool_name,
                status=r.status,
                result=r.result,
            )
            for r in rows
        ]
