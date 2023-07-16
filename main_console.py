import json
import asyncio

import settings
from app.functions.wolframalpha import query_wolframalpha
from app.openai_helpers.chatgpt import ChatGPT, DialogMessage
from app.openai_helpers.count_tokens import count_prompt_tokens
from app.openai_helpers.function_storage import FunctionStorage
from app.openai_helpers.utils import set_openai_token


async def main():
    set_openai_token(settings.OPENAI_TOKEN)
    dialog = []
    function_storage = FunctionStorage()
    function_storage.register(query_wolframalpha)
    chat_gpt = ChatGPT(function_storage=function_storage)

    while True:
        print('Tokens: ', count_prompt_tokens(dialog))
        request_text = input('User: ')
        message = DialogMessage(role="user", content=request_text)
        response, _ = await chat_gpt.send_user_message(message, dialog)
        dialog.append(message)
        if response.function_call:
            dialog.append(response)
            function_name = response.function_call.name
            function_args = json.loads(response.function_call.arguments)
            function_response = await function_storage.run_function(function_name, function_args)
            function_response_obj = DialogMessage(role="function", name=function_name, content=function_response)
            response, _ = await chat_gpt.send_user_message(function_response_obj, dialog)
            dialog.append(function_response_obj)
        print('AI:', response.content)
        print()
        dialog.append(response)


if __name__ == '__main__':
    asyncio.run(main())
