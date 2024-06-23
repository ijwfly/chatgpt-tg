import logging

from typing import Iterable, List
import tiktoken


LOW_DETAIL_COST = 85
HIGH_DETAIL_SQUARE_COST = 170
HIGH_DETAIL_ADDITIONAL_COST = 85

FIRST_SCALE_TO_PX = 2048
SECOND_SCALE_TO_PX = 768


logger = logging.getLogger(__name__)


def get_encoder_for_model(model="gpt-3.5-turbo"):
    if "gpt-3.5-turbo" in model:
        model = "gpt-3.5-turbo"
    elif "gpt-4o" in model:
        model = "gpt-4o"
    elif "gpt-4" in model:
        model = "gpt-4"
    else:
        # TODO: implement custom tokenizers support
        # HACK: fallback to len(str) token counting for unknown models
        return str

    encoding = tiktoken.encoding_for_model(model)
    return encoding.encode


def count_string_tokens(string: str, model="gpt-3.5-turbo") -> int:
    encoder = get_encoder_for_model(model)
    return len(encoder(string))


def extract_tokens_count_from_image_url(image_url):
    # WILD HACK: token count is encoded in the image url in message processor
    try:
        tokens = image_url.split('_')[-1].split('.')[0]
        tokens = int(tokens)
    except (ValueError, IndexError):
        logger.warning(f"Can't extract tokens count from image url: {image_url}")
        # fallback to 1000 tokens
        tokens = 1000
    return tokens


def count_messages_tokens(messages: List[dict], model="gpt-3.5-turbo") -> int:
    # numbers for currently actual models (gpt-3.5-turbo-0613, gpt-4-0314 and older)
    tokens_per_message = 3
    tokens_per_name = 1

    encoder = get_encoder_for_model(model)

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        content = message.get('content')
        if content:
            if isinstance(content, str):
                num_tokens += len(encoder(content))
            elif isinstance(content, list):
                for part in content:
                    if part['type'] == 'text':
                        num_tokens += len(encoder(part['text']))
                    elif part['type'] == 'image_url':
                        num_tokens += extract_tokens_count_from_image_url(part['image_url']['url'])
                    else:
                        ValueError('Unknown content type')

        for key, value in message.items():
            if key == 'content':
                continue
            if value is None:
                continue
            num_tokens += len(encoder(str(value)))
            if key == "name":
                num_tokens += tokens_per_name

    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens


def count_dialog_messages_tokens(messages: Iterable['DialogMessage'], model="gpt-3.5-turbo") -> int:
    return count_messages_tokens([m.openai_message() for m in messages], model)


def count_tokens_from_functions(functions, model="gpt-3.5-turbo"):
    encoder = get_encoder_for_model(model)
    num_tokens = 0
    for function in functions:
        function_tokens = len(encoder(function['name']))
        function_tokens += len(encoder(function['description']))

        if 'parameters' in function:
            parameters = function['parameters']
            if 'properties' in parameters:
                for propertiesKey in parameters['properties']:
                    function_tokens += len(encoder(propertiesKey))
                    v = parameters['properties'][propertiesKey]
                    for field in v:
                        if field == 'type':
                            function_tokens += 2
                            function_tokens += len(encoder(v['type']))
                        elif field == 'description':
                            function_tokens += 2
                            function_tokens += len(encoder(v['description']))
                        elif field == 'enum':
                            function_tokens -= 3
                            for o in v['enum']:
                                function_tokens += 3
                                function_tokens += len(encoder(o))
                function_tokens += 11

        num_tokens += function_tokens

    num_tokens += 12
    return num_tokens


def calculate_image_tokens(width, height, low_detail=False) -> int:
    if low_detail:
        return LOW_DETAIL_COST

    if width > FIRST_SCALE_TO_PX or height > FIRST_SCALE_TO_PX:
        aspect_ratio = width / height
        if width > height:
            width = FIRST_SCALE_TO_PX
            height = int(FIRST_SCALE_TO_PX / aspect_ratio)
        else:
            height = FIRST_SCALE_TO_PX
            width = int(FIRST_SCALE_TO_PX * aspect_ratio)

    if width > height:
        scale_factor = SECOND_SCALE_TO_PX / height
    else:
        scale_factor = SECOND_SCALE_TO_PX / width

    width = int(width * scale_factor)
    height = int(height * scale_factor)

    # count squares of size 512x512
    num_squares_width = (width + 511) // 512  # round up
    num_squares_height = (height + 511) // 512
    total_squares = num_squares_width * num_squares_height

    return total_squares * HIGH_DETAIL_SQUARE_COST + HIGH_DETAIL_ADDITIONAL_COST
