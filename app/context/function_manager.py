from typing import Optional

import settings
from app.functions.wolframalpha import query_wolframalpha
from app.openai_helpers.function_storage import FunctionStorage
from app.storage.db import DB, User


class FunctionManager:
    def __init__(self, db: DB, user: User):
        self.db = db
        self.user = user
        self.function_storage = None

    async def process_functions(self) -> Optional[FunctionStorage]:
        if not self.user.use_functions:
            return None

        functions = []

        if settings.ENABLE_WOLFRAMALPHA:
            functions.append(query_wolframalpha)

        if not functions:
            return None

        function_storage = FunctionStorage()
        for function in functions:
            function_storage.register(function)

        self.function_storage = function_storage
        return function_storage

    def get_function_storage(self) -> Optional[FunctionStorage]:
        return self.function_storage
