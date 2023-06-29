import asyncio

from app import settings
from app.openai_helpers.chatgpt import ChatGPT, DialogueMessage
from app.openai_helpers.count_tokens import count_prompt_tokens
from app.openai_helpers.utils import set_openai_token


async def main():
    set_openai_token(settings.OPENAI_TOKEN)
    dialog = []
    chat_gpt = ChatGPT()

    while True:
        print('Tokens: ', count_prompt_tokens(dialog))
        request_text = input('User: ')
        message = DialogueMessage(role="user", content=request_text)
        response = await chat_gpt.send_user_message(message, dialog)
        print('AI:', response.content)
        print()
        dialog.append(message)
        dialog.append(response)


if __name__ == '__main__':
    asyncio.run(main())
