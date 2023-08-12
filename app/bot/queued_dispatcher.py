from aiogram import Dispatcher, types
import asyncio


class QueuedDispatcher(Dispatcher):
    """
    A custom Dispatcher for aiogram that ensures sequential processing of updates within a single chat.

    This class solves the issue of parallel processing in aiogram by creating a separate queue for each user. If the update
    type is `message`, updates are processed sequentially within the user-specific queue. Other update
    types are processed normally.

    If a user-specific task is already being processed, new tasks from the same user are immediately cancelled, preventing
    parallel execution.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_queues = {}
        self.processing_locks = {}

    async def process_update(self, update: types.Update, **kwargs):
        user_id = None
        if update.message:
            user_id = update.message.chat.id

        if user_id is None:
            # update is not desired type, process normally
            await super().process_update(update, **kwargs)
            return

        if user_id not in self.user_queues:
            self.user_queues[user_id] = asyncio.Queue()
            self.processing_locks[user_id] = asyncio.Lock()

        queue = self.user_queues[user_id]

        await queue.put(update)

        # If lock is already acquired, exit
        if not self.processing_locks[user_id].locked():
            async with self.processing_locks[user_id]:
                while not queue.empty():
                    update = queue.get_nowait()
                    await super().process_update(update, **kwargs)

            del self.user_queues[user_id]  # Cleanup after processing
            del self.processing_locks[user_id]
