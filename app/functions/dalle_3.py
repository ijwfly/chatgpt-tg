from typing import Optional

import httpx
from pydantic import Field

from app.bot.utils import send_photo
from app.context.dialog_manager import DialogUtils
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.openai_helpers.utils import OpenAIAsync


class GenerateImageDalle3Params(OpenAIFunctionParams):
    image_prompt: str = Field(..., description="detailed tailored prompt to generate image from (translated to english, if needed)")


class GenerateImageDalle3(OpenAIFunction):
    PARAMS_SCHEMA = GenerateImageDalle3Params

    @staticmethod
    async def download_image(url):
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise Exception(f'Image download failed with status code {resp.status_code}')
            return resp.content

    async def run(self, params: GenerateImageDalle3Params) -> Optional[str]:
        try:
            resp = await OpenAIAsync.instance().images.generate(
                model="dall-e-3",
                prompt=params.image_prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )

            image_url = resp.data[0].url
            image_bytes = await self.download_image(image_url)

            caption = 'Image generated from prompt:\n'
            caption += params.image_prompt

            response = await send_photo(self.message, image_bytes, caption)
            text = caption + '\n\nImage:\n<image.png>'
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
