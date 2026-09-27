import os
import uuid
import json
from datetime import datetime
from typing import Optional, Any
from deploy_automation.config import settings

os.makedirs("data", exist_ok=True)

try:
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
    from sqlalchemy import String, Integer, DateTime, Text, Boolean, select, update

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    class Base(DeclarativeBase):
        pass

    class ReviewSession(Base):
        __tablename__ = "review_sessions"
        id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        task_id: Mapped[str] = mapped_column(String(64), index=True)
        project_name: Mapped[str] = mapped_column(String(255))
        repo_url: Mapped[str] = mapped_column(String(500))
        reporter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        default_comment: Mapped[str] = mapped_column(Text)
        current_comment: Mapped[str] = mapped_column(Text)
        status: Mapped[str] = mapped_column(String(32), default="PENDING")
        telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        service_needs_json: Mapped[str] = mapped_column(Text, default="[]")
        environment: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
        updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class TaskConversationState(Base):
        __tablename__ = "task_conversation_states"
        id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        task_id: Mapped[str] = mapped_column(String(64), index=True)
        user_telegram_id: Mapped[int] = mapped_column(Integer, index=True)
        step: Mapped[str] = mapped_column(String(64), default="ASSIGNED")
        context_data_json: Mapped[str] = mapped_column(Text, default="{}")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
        updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class ProcessedEvent(Base):
        __tablename__ = "processed_events"
        id: Mapped[str] = mapped_column(String(128), primary_key=True)
        event_type: Mapped[str] = mapped_column(String(64))
        task_id: Mapped[str] = mapped_column(String(64), index=True)
        created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    class TaskProposalMessage(Base):
        __tablename__ = "task_proposal_messages"
        id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        task_id: Mapped[str] = mapped_column(String(64), index=True)
        chat_id: Mapped[int] = mapped_column(Integer)
        message_id: Mapped[int] = mapped_column(Integer)
        message_thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_review_session(
        task_id: str,
        project_name: str,
        repo_url: str,
        default_comment: str,
        reporter_id: Optional[int] = None,
        telegram_message_id: Optional[int] = None,
        service_needs: list = None,
        environment: Optional[str] = None,
    ) -> str:
        session_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            record = ReviewSession(
                id=session_id,
                task_id=task_id,
                project_name=project_name,
                repo_url=repo_url,
                reporter_id=reporter_id,
                default_comment=default_comment,
                current_comment=default_comment,
                status="PENDING",
                telegram_message_id=telegram_message_id,
                service_needs_json=json.dumps(service_needs or [], ensure_ascii=False),
                environment=environment,
            )
            session.add(record)
            await session.commit()
        return session_id

    async def get_review_session(session_id: str) -> Optional[ReviewSession]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ReviewSession).where(ReviewSession.id == session_id))
            return result.scalar_one_or_none()

    async def get_latest_awaiting_edit_session() -> Optional[ReviewSession]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ReviewSession)
                .where(ReviewSession.status == "AWAITING_EDIT_INPUT")
                .order_by(ReviewSession.updated_at.desc())
            )
            return result.scalars().first()

    async def get_pending_handoff_sessions() -> list:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ReviewSession)
                .where(ReviewSession.status == "HANDOFF_LAPTOP")
                .order_by(ReviewSession.updated_at.desc())
            )
            return list(result.scalars().all())

    async def update_session_status(session_id: str, status: str, new_comment: Optional[str] = None, telegram_message_id: Optional[int] = None):
        async with AsyncSessionLocal() as session:
            values = {"status": status, "updated_at": datetime.utcnow()}
            if new_comment is not None:
                values["current_comment"] = new_comment
            if telegram_message_id is not None:
                values["telegram_message_id"] = telegram_message_id
            await session.execute(
                update(ReviewSession)
                .where(ReviewSession.id == session_id)
                .values(**values)
            )
            await session.commit()

    async def save_task_conversation_state(task_id: str, user_telegram_id: int, step: str, context_data: dict = None):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TaskConversationState)
                .where(TaskConversationState.task_id == task_id, TaskConversationState.user_telegram_id == user_telegram_id)
            )
            state_rec = result.scalar_one_or_none()
            if not state_rec:
                state_rec = TaskConversationState(
                    task_id=task_id,
                    user_telegram_id=user_telegram_id,
                    step=step,
                    context_data_json=json.dumps(context_data or {}, ensure_ascii=False)
                )
                session.add(state_rec)
            else:
                state_rec.step = step
                if context_data is not None:
                    existing = {}
                    try:
                        existing = json.loads(state_rec.context_data_json or "{}")
                    except Exception:
                        pass
                    existing.update(context_data)
                    state_rec.context_data_json = json.dumps(existing, ensure_ascii=False)
                state_rec.updated_at = datetime.utcnow()
            await session.commit()

    async def get_task_conversation_state(task_id: str, user_telegram_id: int) -> Optional[TaskConversationState]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TaskConversationState)
                .where(TaskConversationState.task_id == task_id, TaskConversationState.user_telegram_id == user_telegram_id)
            )
            return result.scalar_one_or_none()

    async def get_user_active_conversations(user_telegram_id: int) -> list[TaskConversationState]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TaskConversationState)
                .where(
                    TaskConversationState.user_telegram_id == user_telegram_id,
                    TaskConversationState.step != "COMPLETED",
                    TaskConversationState.step != "CLOSED"
                )
                .order_by(TaskConversationState.updated_at.desc())
            )
            return list(result.scalars().all())

    async def is_event_processed(event_id: str) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ProcessedEvent).where(ProcessedEvent.id == event_id))
            return result.scalar_one_or_none() is not None

    async def mark_event_processed(event_id: str, event_type: str, task_id: str):
        async with AsyncSessionLocal() as session:
            event = ProcessedEvent(id=event_id, event_type=event_type, task_id=task_id)
            session.add(event)
            await session.commit()

    async def save_task_proposal_message(task_id: str, chat_id: int, message_id: int, message_thread_id: Optional[int] = None):
        async with AsyncSessionLocal() as session:
            rec = TaskProposalMessage(
                task_id=task_id,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id
            )
            session.add(rec)
            await session.commit()

    async def get_task_proposal_messages(task_id: str) -> list[TaskProposalMessage]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TaskProposalMessage).where(TaskProposalMessage.task_id == task_id)
            )
            return list(result.scalars().all())

except ImportError:
    class ReviewSession:
        pass
    class TaskProposalMessage:
        pass
    AsyncSessionLocal = None
    async def init_db():
        pass
    async def create_review_session(*args, **kwargs):
        return str(uuid.uuid4())
    async def get_review_session(*args, **kwargs):
        return None
    async def get_latest_awaiting_edit_session(*args, **kwargs):
        return None
    async def get_pending_handoff_sessions(*args, **kwargs):
        return []
    async def save_task_conversation_state(*args, **kwargs):
        pass
    async def get_task_conversation_state(*args, **kwargs):
        return None
    async def get_user_active_conversations(*args, **kwargs):
        return []
    async def is_event_processed(*args, **kwargs):
        return False
    async def mark_event_processed(*args, **kwargs):
        pass
    async def save_task_proposal_message(*args, **kwargs):
        pass
    async def get_task_proposal_messages(*args, **kwargs):
        return []
