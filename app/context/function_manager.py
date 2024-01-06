from typing import Optional

import settings
from app.functions.dalle_3 import GenerateImageDalle3
from app.functions.save_user_settings import SaveUserSettings
from app.functions.wolframalpha import QueryWolframAlpha
from app.openai_helpers.function_storage import FunctionStorage
from app.storage.db import DB, User
from app.storage.user_role import check_access_conditions
from settings import USER_ROLE_IMAGE_GENERATION


class FunctionManager:
    def __init__(self, db: DB, user: User):
        self.db = db
        self.user = user
        self.function_storage = None

    @staticmethod
    def get_static_functions():
        functions = []

        if settings.ENABLE_WOLFRAMALPHA:
            functions.append(QueryWolframAlpha)

        return functions

    def get_conditional_functions(self):
        functions = []

        if self.user.image_generation and check_access_conditions(USER_ROLE_IMAGE_GENERATION, self.user.role):
            functions.append(GenerateImageDalle3)

        # TODO: add setting to disable this feature
        functions.append(SaveUserSettings)

        return functions

    async def process_functions(self) -> Optional[FunctionStorage]:
        if not self.user.use_functions:
            return None

        functions = self.get_static_functions()
        functions += self.get_conditional_functions()

        if not functions:
            return None

        function_storage = FunctionStorage()
        for function in functions:
            function_storage.register(function)

        self.function_storage = function_storage
        return function_storage

    def get_function_storage(self) -> Optional[FunctionStorage]:
        return self.function_storage
