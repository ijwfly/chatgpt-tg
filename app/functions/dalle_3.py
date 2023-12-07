from typing import Optional

from pydantic import Field

from app.bot.utils import send_telegram_message
from app.context.dialog_manager import DialogUtils
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.openai_helpers.utils import OpenAIAsync


class GenerateImageDalle3Params(OpenAIFunctionParams):
    image_prompt: str = Field(..., description="detailed tailored prompt to generate image from (translated to english, if needed)")


class GenerateImageDalle3(OpenAIFunction):
    PARAMS_SCHEMA = GenerateImageDalle3Params

    async def run(self, params: GenerateImageDalle3Params) -> Optional[str]:
        try:
            resp = await OpenAIAsync.instance().images.generate(
                model="dall-e-3",
                prompt=params.image_prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            text = 'Image generated from prompt:\n'
            text += params.image_prompt
            text += '\n\n'
            text += 'URL:\n'
            text += resp.data[0].url

            response = await send_telegram_message(self.message, text)
            dialog_message = DialogUtils.prepare_function_response(self.get_name(), text)
            await self.context_manager.add_message(dialog_message, response.message_id)
            return None
        except Exception as e:
            return f"Error: {e}"

    @classmethod
    def get_name(cls) -> str:
        return "generate_image_dalle_3"

    @classmethod
    def get_description(cls) -> str:
        return "Use dalle-3 to generate image from prompt. Image prompt must be in english. Generate tailored detailed prompt for user idea."
