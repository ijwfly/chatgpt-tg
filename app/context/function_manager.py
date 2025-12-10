from typing import Optional

import logging
import settings
from app.context.dialog_manager import DialogManager
from app.functions.dalle_3 import GenerateImageDalle3
from app.functions.mcp.mcp_function_storage import MCPFunctionManager
from app.functions.obsidian_echo import CreateObsidianNote
from app.functions.save_user_settings import SaveUserSettings
from app.functions.todoist import TodoistAddTask
from app.functions.vectara_search import VectorSearch
from app.functions.wolframalpha import QueryWolframAlpha
from app.openai_helpers.function_storage import FunctionStorage
from app.storage.db import DB, User, MessageType
from app.storage.user_role import check_access_conditions
from settings import USER_ROLE_IMAGE_GENERATION


logger = logging.getLogger(__name__)


class FunctionManager:
    def __init__(self, db: DB, user: User, dialog_manager: DialogManager):
        self.db = db
        self.user = user
        self.function_storage = None
        self.dialog_manager = dialog_manager

    @staticmethod
    def get_static_functions():
        functions = []

        if settings.ENABLE_WOLFRAMALPHA:
            functions.append(QueryWolframAlpha)

        return functions

    def get_conditional_functions(self):
        functions = []

        if self.user.telegram_id == settings.USER_ROLE_MANAGER_CHAT_ID and settings.ENABLE_TODOIST_ADMIN_INTEGRATION:
            functions.append(TodoistAddTask)

        if self.user.telegram_id == settings.USER_ROLE_MANAGER_CHAT_ID and settings.ENABLE_OBSIDIAN_ECHO_ADMIN_INTEGRATION:
            functions.append(CreateObsidianNote)

        if self.user.image_generation and check_access_conditions(USER_ROLE_IMAGE_GENERATION, self.user.role):
            functions.append(GenerateImageDalle3)

        if self.user.system_prompt_settings_enabled:
            functions.append(SaveUserSettings)

        if settings.VECTARA_RAG_ENABLED:
            messages = self.dialog_manager.messages
            context_has_documents = any(m.message_type == MessageType.DOCUMENT for m in messages)
            if context_has_documents:
                functions.append(VectorSearch)

        return functions

    async def process_functions(self) -> Optional[FunctionStorage]:
        if not self.user.use_functions:
            return None

        functions = self.get_static_functions()
        functions += self.get_conditional_functions()

        for mcp_config in settings.MCP_SERVERS:
            if check_access_conditions(mcp_config.min_role, self.user.role):
                mcp_manager = MCPFunctionManager(mcp_config.url, mcp_config.headers)
                try:
                    functions += await mcp_manager.get_tools()
                except Exception as e:
                    logger.error("Error while getting tools: {}. Skipping MCP tools.".format(e))

        if not functions:
            return None

        function_storage = FunctionStorage()
        for function in functions:
            function_storage.register(function)

        self.function_storage = function_storage
        return function_storage

    def get_function_storage(self) -> Optional[FunctionStorage]:
        return self.function_storage
