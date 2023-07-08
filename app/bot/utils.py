import dataclasses
import re
import asyncio
from typing import List
from contextlib import asynccontextmanager

TYPING_TIMEOUT = 180
TYPING_DELAY = 2
TYPING_QUERIES_LIMIT = TYPING_TIMEOUT // TYPING_DELAY


class TypingWorker:
    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id
        self.typing_task = None
        self.typing_queries_count = 0

    @asynccontextmanager
    async def typing_context(self):
        await self.start_typing()
        yield
        await self.stop_typing()

    async def start_typing(self):
        async def typing_worker():
            while self.typing_queries_count < TYPING_QUERIES_LIMIT:
                await self.bot.send_chat_action(self.chat_id, 'typing')
                await asyncio.sleep(TYPING_DELAY)
                self.typing_queries_count += 1

        self.typing_task = asyncio.create_task(typing_worker())
        return self

    async def stop_typing(self):
        if self.typing_task is not None:
            self.typing_task.cancel()
            try:
                await self.typing_task
            except asyncio.CancelledError:
                pass
            self.typing_task = None


@dataclasses.dataclass
class CodeFragment:
    language: str
    code: str


def detect_and_extract_code(text) -> List[CodeFragment]:
    pattern = r"```(\S+)\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    results = []
    for match in matches:
        language, code = match
        fragment = CodeFragment(language, code)
        results.append(fragment)
    return results
