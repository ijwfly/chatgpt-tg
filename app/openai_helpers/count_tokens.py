from typing import Iterable

import tiktoken

from app.openai_helpers.chatgpt import DialogMessage


def count_prompt_tokens(messages: Iterable[DialogMessage], model="gpt-3.5-turbo") -> int:
    if "gpt-3.5-turbo" in model:
        model = "gpt-3.5-turbo"
        tokens_per_message = 4
        tokens_per_name = -1
    elif "gpt-4" in model:
        model = "gpt-4"
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        raise ValueError(f"Unknown model: {model}")

    encoding = tiktoken.encoding_for_model(model)

    openai_messages = (m.openai_message() for m in messages)

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
