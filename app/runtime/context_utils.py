import logging
from typing import Iterable, Set
from urllib.parse import urljoin

import settings
from app.context.context_manager import ContextManager
from app.context.dialog_manager import DialogUtils
from app.context.tool_call_repair import interrupted_tool_response
from app.openai_helpers.count_tokens import calculate_image_tokens
from app.runtime.image_refs import format_image_label, next_image_number
from app.runtime.user_input import UserInput

logger = logging.getLogger(__name__)


async def add_user_input_to_context(user_input: UserInput, context_manager: ContextManager):
    """Add all items from UserInput to context. Used by both runtime and context-only path."""
    # Add voice transcriptions
    for vt in user_input.voice_transcriptions:
        dialog_message = DialogUtils.prepare_user_message(vt.text)
        await context_manager.add_message(
            dialog_message, vt.tg_message_id, alias_tg_message_ids=vt.alias_tg_message_ids,
        )

    # Add sandbox workspace files
    for sf in user_input.sandbox_files:
        text = f'[file uploaded to agent workspace] {sf.filename} ({sf.size} bytes)'
        if sf.caption:
            # the caption belongs to the same telegram message, so it goes into the same context message
            text = f'{text}\n{sf.caption}'
        dialog_message = DialogUtils.prepare_user_message(text)
        await context_manager.add_message(
            dialog_message, sf.tg_message_id, alias_tg_message_ids=sf.alias_tg_message_ids,
        )

    # Add text/image messages
    image_number = None
    for text_input in user_input.text_inputs:
        if text_input.images:
            if image_number is None:
                image_number = next_image_number(context_manager.dialog_manager.get_dialog_messages())
            content = []
            if text_input.text:
                content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_TEXT, text_input.text))
            for img in text_input.images:
                tokens = calculate_image_tokens(img.width, img.height)
                file_url = urljoin(
                    f'{settings.IMAGE_PROXY_URL}:{settings.IMAGE_PROXY_PORT}',
                    f'{img.file_id}_{tokens}.jpg',
                )
                # the label lets the model point at this exact image later, e.g. to save it as a file
                content.append(DialogUtils.construct_message_content_part(
                    DialogUtils.CONTENT_TEXT, format_image_label(image_number)))
                content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_IMAGE_URL, file_url))
                image_number += 1
            dialog_message = DialogUtils.prepare_user_message(content)
        elif text_input.text:
            dialog_message = DialogUtils.prepare_user_message(text_input.text)
        else:
            continue
        await context_manager.add_message(dialog_message, text_input.tg_message_id)


async def close_unanswered_tool_calls(context_manager: ContextManager, tool_calls: Iterable, answered_ids: Set[str]):
    """
    Saves an error result for every tool call that got no result, so the assistant message with
    `tool_calls` already stored in the dialog does not break every later request of this branch.
    Called when the tool loop is interrupted (cancellation, transport error, generator close);
    best effort — a failure here is only logged.
    """
    for tool_call in tool_calls or []:
        if not tool_call.id or tool_call.id in answered_ids:
            continue
        try:
            await context_manager.add_message(interrupted_tool_response(tool_call.id), -1)
        except Exception as e:
            logger.warning(f'Could not close interrupted tool call {tool_call.id}: {e}')
