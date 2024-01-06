from typing import Optional

from app.bot.utils import send_telegram_message
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from pydantic import Field


class SaveUserSettingsParams(OpenAIFunctionParams):
    settings_text: str = Field(..., description='full list of user info and settings which will apear in <UserSettings> block in system prompt')


class SaveUserSettings(OpenAIFunction):
    PARAMS_SCHEMA = SaveUserSettingsParams

    async def run(self, params: SaveUserSettingsParams) -> Optional[str]:
        self.user.system_prompt_settings = params.settings_text
        await self.db.update_user(self.user)
        await send_telegram_message(self.message, f'Saved User Info:\n{params.settings_text}')
        return 'success'

    @classmethod
    def get_name(cls) -> str:
        return "save_user_settings"

    @classmethod
    def get_description(cls) -> str:
        return "Save user info or user settings when user asks to do so. Rewrite the text of the UserSettings in system prompt."
