from aiogram import Bot

from fastapi import FastAPI, HTTPException
from starlette.responses import StreamingResponse

import httpx
import uvicorn

import settings

app = FastAPI()
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)


@app.get("/{file_id}_{tokens}.jpg")
async def get_file(file_id: str, tokens: str):
    try:
        # Получаем информацию о файле из Telegram
        file_info = await bot.get_file(file_id)
        file_url = bot.get_file_url(file_info.file_path)

        # Делаем HEAD-запрос для получения заголовков, включая Content-Length
        async with httpx.AsyncClient() as client:
            head_response = await client.head(file_url)

        # Извлекаем Content-Length и Content-Type
        content_length = head_response.headers.get("Content-Length")
        content_type = head_response.headers.get("Content-Type", "application/octet-stream")

        # Подготавливаем заголовки для ответа
        headers = {}
        if content_length:
            headers["Content-Length"] = content_length

        # Функция для потоковой передачи контента
        async def stream_response():
            async with httpx.AsyncClient() as client:
                async with client.stream('GET', file_url) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk

        return StreamingResponse(
            stream_response(),
            media_type=content_type,
            headers=headers
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving file: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(app, host=settings.IMAGE_PROXY_BIND_HOST, port=settings.IMAGE_PROXY_BIND_PORT)
