"""Referring to images the user sent earlier in the dialog.

An image reaches the context as an `image_url` content part pointing at the image proxy, and the
telegram file_id is the last path segment of that url (`<file_id>_<tokens>.jpg`). Every image also
gets a text label `[image #N]` right before it, so the model can name one when it wants the file
itself (see `save_image_to_workspace` in app/functions/bash_sandbox.py).
"""
import os
import re
from typing import Iterator, List, Optional, Tuple

from app.openai_helpers.chatgpt import DialogMessage

IMAGE_LABEL_RE = re.compile(r'\[image #(\d+)]')


def format_image_label(number: int) -> str:
    return f'[image #{number}]'


def file_id_from_image_url(url: str) -> Optional[str]:
    """Recovers the telegram file_id from a proxy url built by add_user_input_to_context.

    file_ids contain both `-` and `_`, so only the last underscore (the token count added for
    `extract_tokens_count_from_image_url`) may be cut off.
    """
    name = os.path.basename(url or '')
    name = name.rsplit('.', 1)[0]
    file_id, _, tokens = name.rpartition('_')
    if not file_id or not tokens.isdigit():
        return None
    return file_id


def _message_texts(message: DialogMessage) -> Iterator[str]:
    content = message.content
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if part.type == 'text' and part.text:
                yield part.text


def next_image_number(messages: List[DialogMessage]) -> int:
    """Numbering continues across the whole dialog branch, so labels stay unambiguous."""
    highest = 0
    for message in messages:
        for text in _message_texts(message):
            for match in IMAGE_LABEL_RE.finditer(text):
                highest = max(highest, int(match.group(1)))
    return highest + 1


def collect_labeled_images(messages: List[DialogMessage]) -> List[Tuple[Optional[int], str]]:
    """(label number, file_id) for every image in the branch, in dialog order.

    Images stored before labels existed carry no number, but are still reachable as "the last one".
    """
    images = []
    for message in messages:
        if not isinstance(message.content, list):
            continue
        label: Optional[int] = None
        for part in message.content:
            if part.type == 'text' and part.text:
                match = IMAGE_LABEL_RE.search(part.text)
                if match:
                    label = int(match.group(1))
            elif part.type == 'image_url' and part.image_url:
                file_id = file_id_from_image_url(part.image_url.url)
                if file_id:
                    images.append((label, file_id))
                label = None
    return images


def find_image(messages: List[DialogMessage],
               image_id: Optional[int] = None) -> Optional[Tuple[Optional[int], str]]:
    """The image carrying `image_id`, or the most recent one when no id is given."""
    images = collect_labeled_images(messages)
    if not images:
        return None
    if image_id is None:
        return images[-1]
    for number, file_id in images:
        if number == image_id:
            return number, file_id
    return None
