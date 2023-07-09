from typing import List

import tiktoken

from app.openai_helpers.chatgpt import DialogMessage


def count_prompt_tokens(messages: List[DialogMessage], model="gpt-3.5-turbo") -> int:
    encoding = tiktoken.encoding_for_model(model)

    if model == "gpt-3.5-turbo":
        tokens_per_message = 4
        tokens_per_name = -1
    elif model == "gpt-4":
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        raise ValueError(f"Unknown model: {model}")

    openai_messages = []
    for message in messages:
        message = message.openai_message()
        openai_messages.append(message)

    tokens_count = 0
    for message in openai_messages:
        tokens_count += tokens_per_message
        for key, value in message.items():
            if value is None:
                continue
            tokens_count += len(encoding.encode(str(value)))
            if key == "name":
                tokens_count += tokens_per_name

    tokens_count += 2

    return tokens_count
