import asyncio

import settings
from app.bot.queued_dispatcher import QueuedDispatcher
from app.bot.user_role_manager import UserRoleManager

from aiogram import Bot

from app.storage.db import DBFactory

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = QueuedDispatcher(bot)


async def main():
    db = await DBFactory().create_database(
        settings.POSTGRES_USER, settings.POSTGRES_PASSWORD,
        settings.POSTGRES_HOST, settings.POSTGRES_PORT, settings.POSTGRES_DATABASE
    )

    try:
        user_role_manager = UserRoleManager(bot, dp, db)
        async for user in db.iterate_users():
            await user_role_manager.send_new_user_to_admin(bot, user)
            print(f"User id: {user.id}, telegram_id: {user.telegram_id} sent to admin")
            await asyncio.sleep(1)
    finally:
        await DBFactory.close_database()
        session = await bot.get_session()
        await session.close()


if __name__ == '__main__':
    asyncio.run(main())
