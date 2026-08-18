from urllib.parse import urljoin

import settings
from app.context.context_manager import ContextManager
from app.context.dialog_manager import DialogUtils
from app.openai_helpers.count_tokens import calculate_image_tokens
from app.runtime.user_input import UserInput


async def add_user_input_to_context(user_input: UserInput, context_manager: ContextManager):
    """Add all items from UserInput to context. Used by both runtime and context-only path."""
    # Add voice transcriptions
    for vt in user_input.voice_transcriptions:
        dialog_message = DialogUtils.prepare_user_message(vt.text)
        await context_manager.add_message(dialog_message, vt.tg_message_id)

    # Add sandbox workspace files
    for sf in user_input.sandbox_files:
        dialog_message = DialogUtils.prepare_user_message(
            f'[file uploaded to agent workspace] {sf.filename} ({sf.size} bytes)'
        )
        await context_manager.add_message(
            dialog_message, sf.tg_message_id, alias_tg_message_ids=sf.alias_tg_message_ids,
        )

    # Add text/image messages
    for text_input in user_input.text_inputs:
        if text_input.images:
            content = []
            if text_input.text:
                content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_TEXT, text_input.text))
            for img in text_input.images:
                tokens = calculate_image_tokens(img.width, img.height)
                file_url = urljoin(
                    f'{settings.IMAGE_PROXY_URL}:{settings.IMAGE_PROXY_PORT}',
                    f'{img.file_id}_{tokens}.jpg',
                )
                content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_IMAGE_URL, file_url))
            dialog_message = DialogUtils.prepare_user_message(content)
        elif text_input.text:
            dialog_message = DialogUtils.prepare_user_message(text_input.text)
        else:
            continue
        await context_manager.add_message(dialog_message, text_input.tg_message_id)
