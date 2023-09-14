from typing import Iterable, List

import tiktoken


def count_string_tokens(string: str, model="gpt-3.5-turbo") -> int:
    if "gpt-3.5-turbo" in model:
        model = "gpt-3.5-turbo"
    elif "gpt-4" in model:
        model = "gpt-4"
    else:
        raise ValueError(f"Unknown model: {model}")
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(str(string)))


def count_messages_tokens(messages: List[dict], model="gpt-3.5-turbo") -> int:
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

    tokens_count = 0
    for message in messages:
        tokens_count += tokens_per_message
        for key, value in message.items():
            if value is None:
                continue
            tokens_count += len(encoding.encode(str(value)))
            if key == "name":
                tokens_count += tokens_per_name

    tokens_count += 2

    return tokens_count


def count_dialog_messages_tokens(messages: Iterable['DialogMessage'], model="gpt-3.5-turbo") -> int:
    return count_messages_tokens([m.openai_message() for m in messages], model)


def count_tokens_from_functions(functions, model="gpt-3.5-turbo"):
    if "gpt-3.5-turbo" in model:
        model = "gpt-3.5-turbo"
    elif "gpt-4" in model:
        model = "gpt-4"
    else:
        raise ValueError(f"Unknown model: {model}")

    encoding = tiktoken.encoding_for_model(model)
    num_tokens = 0
    for function in functions:
        function_tokens = len(encoding.encode(function['name']))
        function_tokens += len(encoding.encode(function['description']))

        if 'parameters' in function:
            parameters = function['parameters']
            if 'properties' in parameters:
                for propertiesKey in parameters['properties']:
                    function_tokens += len(encoding.encode(propertiesKey))
                    v = parameters['properties'][propertiesKey]
                    for field in v:
                        if field == 'type':
                            function_tokens += 2
                            function_tokens += len(encoding.encode(v['type']))
                        elif field == 'description':
                            function_tokens += 2
                            function_tokens += len(encoding.encode(v['description']))
                        elif field == 'enum':
                            function_tokens -= 3
                            for o in v['enum']:
                                function_tokens += 3
                                function_tokens += len(encoding.encode(o))
                        else:
                            print(f"Warning: not supported field {field}")
                function_tokens += 11

        num_tokens += function_tokens

    num_tokens += 12
    return num_tokens
