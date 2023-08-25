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

    @staticmethod
    def get_static_functions():
        functions = []

        if settings.ENABLE_WOLFRAMALPHA:
            functions.append(query_wolframalpha)

        return functions

    async def process_functions(self) -> Optional[FunctionStorage]:
        if not self.user.use_functions:
            return None

        functions = self.get_static_functions()
        if not functions:
            return None

        function_storage = FunctionStorage()
        for function in functions:
            function_storage.register(function)

        self.function_storage = function_storage
        return function_storage

    def get_function_storage(self) -> Optional[FunctionStorage]:
        return self.function_storage
