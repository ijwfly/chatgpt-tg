import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter

from aiogram import Bot

import settings
from app.bot.bot_side_effects import BotSideEffectHandler
from app.context.context_manager import build_context_manager
from app.runtime.agent_runtime import AgentRuntime
from app.runtime.conversation_session import ConversationSession
from app.runtime.events import FinalResponse
from app.runtime.user_input import UserInput, TextInput
from app.storage.db import DB

logger = logging.getLogger(__name__)


def compute_next_cron(cron_expression: str, base_time: datetime = None) -> datetime:
    """Compute next execution time from a cron expression."""
    if base_time is None:
        base_time = datetime.now(timezone.utc)
    return croniter(cron_expression, base_time).get_next(datetime)


class SchedulerService:
    """Background service that polls for due scheduled tasks and executes them."""

    def __init__(self, bot: Bot, db: DB):
        self.bot = bot
        self.db = db
        self._task: asyncio.Task = None

    def start(self):
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("SchedulerService started")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("SchedulerService stopped")

    async def _poll_loop(self):
        while True:
            try:
                await asyncio.sleep(settings.SCHEDULER_POLL_INTERVAL)
                now = datetime.now(timezone.utc)
                due_tasks = await self.db.get_due_tasks(now)
                for task_record in due_tasks:
                    asyncio.create_task(self._execute_task(task_record))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"SchedulerService poll error: {e}")

    async def _execute_task(self, task_record: dict):
        """Execute a scheduled task as a full agent turn."""
        task_id = task_record['id']
        try:
            # Update execution state FIRST to avoid re-triggering
            now = datetime.now(timezone.utc)
            if task_record['schedule_type'] == 'once':
                await self.db.disable_scheduled_task(task_id)
            else:
                next_exec = compute_next_cron(task_record['cron_expression'], now)
                await self.db.update_scheduled_task_execution(task_id, now, next_exec)

            user = await self.db.get_user_by_id(task_record['user_id'])
            if user is None:
                logger.error(f"Scheduled task {task_id}: user {task_record['user_id']} not found")
                return

            chat_id = task_record['chat_id']
            title = task_record['title']
            prompt = task_record['prompt']

            # Notify chat that a scheduled task is starting
            side_effects = BotSideEffectHandler(self.bot, chat_id)
            notify_msg_id = await side_effects.send_message(f"⏰ Scheduled task: {title}")

            # Build synthetic input and session
            user_input = UserInput(text_inputs=[
                TextInput(text=f"[Scheduled task: {title}]\n{prompt}")
            ])
            session = ConversationSession(chat_id=chat_id)

            # Run full agent turn
            context_manager = await build_context_manager(self.db, user, session)
            runtime = AgentRuntime(self.db, user, side_effects, context_manager)

            final_text = ""
            async for event in runtime.process_turn(user_input, session, lambda: False):
                if isinstance(event, FinalResponse) and event.dialog_message.content:
                    final_text = event.dialog_message.get_text_content()

            if final_text:
                await self.bot.send_message(chat_id, final_text)

            logger.info(f"Scheduled task {task_id} '{title}' executed for chat {chat_id}")

        except Exception as e:
            logger.error(f"Error executing scheduled task {task_id}: {e}")
