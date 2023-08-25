import asyncio

import settings
from app.bot.queued_dispatcher import QueuedDispatcher
from app.bot.user_role_manager import UserRoleManager
from app.storage.db import DBFactory

from aiogram import Bot


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
            await user_role_manager.set_user_commands(user)
            print(f"User id: {user.id}, telegram_id: {user.telegram_id} keyboard updated")
            await asyncio.sleep(0.3)
    finally:
        await DBFactory.close_database()
        session = await bot.get_session()
        await session.close()


if __name__ == '__main__':
    asyncio.run(main())
