from aiogram import Bot

from fastapi import FastAPI
from starlette.responses import StreamingResponse

import httpx
import uvicorn

import settings

app = FastAPI()
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)


@app.get("/{file_id}.jpg")
async def get_file(file_id: str):
    file_info = await bot.get_file(file_id)
    file_url = bot.get_file_url(file_info.file_path)

    async def stream_response():
        async with httpx.AsyncClient() as client:
            async with client.stream('GET', file_url) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk

    content_type = "application/octet-stream"
    return StreamingResponse(stream_response(), media_type=content_type)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.IMAGE_PROXY_BIND_HOST, port=settings.IMAGE_PROXY_BIND_PORT)
