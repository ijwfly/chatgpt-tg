import asyncio
import datetime
import pytz
import logging

import settings
from app.bot.utils import get_completion_usage_response_all_users

FAIL_LIMIT = 5
WAIT_BETWEEN_RETRIES = 5

logger = logging.getLogger(__name__)


class MonthlyTask:
    def __init__(self, task, timezone='UTC'):
        self.timezone = pytz.timezone(timezone)
        self.current_month = datetime.datetime.now(self.timezone).month
        self.task_function = task
        self.fail_counter = 0
        self.task = None

    async def _check_date_and_execute(self):
        while True:
            now = datetime.datetime.now(self.timezone)
            if now.month != self.current_month:
                try:
                    await self.task_function()
                except Exception as e:
                    logger.exception(f"An error occurred while executing the task: %s", e)
                    if self.fail_counter < FAIL_LIMIT:
                        await asyncio.sleep(WAIT_BETWEEN_RETRIES)
                        self.fail_counter += 1
                        continue
                    else:
                        logger.error(f"Task {self.task_function} failed {FAIL_LIMIT} times, skipping until next day")
                        self.fail_counter = 0

                self.current_month = now.month

            # Calculate time until the start of the next day
            tomorrow = now + datetime.timedelta(days=1)
            start_of_next_day = datetime.datetime(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day,
                                                  tzinfo=self.timezone)
            seconds_until_next_day = (start_of_next_day - now).total_seconds()

            await asyncio.sleep(seconds_until_next_day)  # Sleep until the start of the next day

    def start(self):
        self.task = asyncio.create_task(self._check_date_and_execute())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None


def build_monthly_usage_task(bot, db) -> MonthlyTask:
    async def get_monthly_usage():
        if not settings.ENABLE_USER_ROLE_MANAGER_CHAT:
            return

        previous_month = datetime.datetime.now(settings.POSTGRES_TIMEZONE).replace(day=1) - datetime.timedelta(days=1)
        previous_month = previous_month.date()
        result = await get_completion_usage_response_all_users(db, previous_month)
        await bot.send_message(
            settings.USER_ROLE_MANAGER_CHAT_ID, result
        )
    return MonthlyTask(get_monthly_usage)
