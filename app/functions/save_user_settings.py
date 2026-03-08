from typing import Optional

from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from pydantic import Field


class SaveUserSettingsParams(OpenAIFunctionParams):
    settings_text: str = Field(..., description='full list of user info and settings which will apear in <UserSettings> block in system prompt')


class SaveUserSettings(OpenAIFunction):
    PARAMS_SCHEMA = SaveUserSettingsParams

    async def run(self, params: SaveUserSettingsParams) -> Optional[str]:
        self.user.system_prompt_settings = params.settings_text.strip()
        await self.db.update_user(self.user)
        if self.user.system_prompt_settings:
            await self.side_effects.send_message(f'Saved User Info:\n{params.settings_text}')
        else:
            await self.side_effects.send_message(f'Cleared User Info')
        return 'success'

    @classmethod
    def get_name(cls) -> str:
        return "save_user_settings"

    @classmethod
    def get_description(cls) -> str:
        return "Save user info or user settings when user asks to do so. Rewrite the text of the UserSettings in system prompt."
