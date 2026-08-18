import time
from aiogram import types

_update_id_counter = 100000
_message_id_counter = 1000


def _next_update_id():
    global _update_id_counter
    _update_id_counter += 1
    return _update_id_counter


def _next_message_id():
    global _message_id_counter
    _message_id_counter += 1
    return _message_id_counter


def _make_user_dict(user_id=12345, first_name='Test', last_name='User', username='testuser'):
    return {
        'id': user_id,
        'is_bot': False,
        'first_name': first_name,
        'last_name': last_name,
        'username': username,
    }


def _make_chat_dict(chat_id=12345):
    return {
        'id': chat_id,
        'type': 'private',
    }


def make_text_message(text, user_id=12345, chat_id=None, reply_to_message_id=None,
                      first_name='Test', last_name='User', username='testuser'):
    if chat_id is None:
        chat_id = user_id

    message_id = _next_message_id()
    message_dict = {
        'message_id': message_id,
        'from': _make_user_dict(user_id, first_name, last_name, username),
        'chat': _make_chat_dict(chat_id),
        'date': int(time.time()),
        'text': text,
    }

    if reply_to_message_id is not None:
        message_dict['reply_to_message'] = {
            'message_id': reply_to_message_id,
            'from': {'id': 0, 'is_bot': True, 'first_name': 'Bot'},
            'chat': _make_chat_dict(chat_id),
            'date': int(time.time()),
            'text': '...',
        }

    update_dict = {
        'update_id': _next_update_id(),
        'message': message_dict,
    }
    return types.Update(**update_dict)


def make_document_message(file_name, file_id='test-doc-file-id', file_size=100,
                          user_id=12345, chat_id=None, caption=None, reply_to_message_id=None):
    if chat_id is None:
        chat_id = user_id

    message_id = _next_message_id()
    message_dict = {
        'message_id': message_id,
        'from': _make_user_dict(user_id),
        'chat': _make_chat_dict(chat_id),
        'date': int(time.time()),
        'document': {
            'file_id': file_id,
            'file_unique_id': f'unique-{file_id}',
            'file_name': file_name,
            'file_size': file_size,
        },
    }
    if caption is not None:
        message_dict['caption'] = caption

    if reply_to_message_id is not None:
        message_dict['reply_to_message'] = {
            'message_id': reply_to_message_id,
            'from': {'id': 0, 'is_bot': True, 'first_name': 'Bot'},
            'chat': _make_chat_dict(chat_id),
            'date': int(time.time()),
            'text': '...',
        }

    update_dict = {
        'update_id': _next_update_id(),
        'message': message_dict,
    }
    return types.Update(**update_dict)


def make_video_note_message(file_id='test-video-note-id', file_size=2048, duration=5,
                            length=240, user_id=12345, chat_id=None):
    if chat_id is None:
        chat_id = user_id

    message_id = _next_message_id()
    message_dict = {
        'message_id': message_id,
        'from': _make_user_dict(user_id),
        'chat': _make_chat_dict(chat_id),
        'date': int(time.time()),
        'video_note': {
            'file_id': file_id,
            'file_unique_id': f'unique-{file_id}',
            'length': length,
            'duration': duration,
            'file_size': file_size,
        },
    }

    update_dict = {
        'update_id': _next_update_id(),
        'message': message_dict,
    }
    return types.Update(**update_dict)


def make_command_message(command, user_id=12345, chat_id=None, **kwargs):
    return make_text_message(f'/{command}', user_id=user_id, chat_id=chat_id, **kwargs)


def make_forward_message(text, forward_sender_name=None, forward_from=None,
                         user_id=12345, chat_id=None, photo_file_id=None, caption=None,
                         **kwargs):
    if chat_id is None:
        chat_id = user_id

    message_id = _next_message_id()
    message_dict = {
        'message_id': message_id,
        'from': _make_user_dict(user_id, **{k: v for k, v in kwargs.items()
                                            if k in ('first_name', 'last_name', 'username')}),
        'chat': _make_chat_dict(chat_id),
        'date': int(time.time()),
        'forward_date': int(time.time()),
    }

    if text is not None:
        message_dict['text'] = text
    if caption is not None:
        message_dict['caption'] = caption
    if photo_file_id is not None:
        message_dict['photo'] = [{
            'file_id': photo_file_id,
            'file_unique_id': f'unique-{photo_file_id}',
            'width': 800,
            'height': 600,
            'file_size': 1024,
        }]

    if forward_from:
        message_dict['forward_from'] = {
            'id': forward_from.get('id', 99999),
            'is_bot': False,
            'first_name': forward_from.get('first_name', 'Forwarded'),
        }
    elif forward_sender_name:
        message_dict['forward_sender_name'] = forward_sender_name

    update_dict = {
        'update_id': _next_update_id(),
        'message': message_dict,
    }
    return types.Update(**update_dict)


def make_callback_query(data, message_id, user_id=12345, chat_id=None):
    if chat_id is None:
        chat_id = user_id

    update_dict = {
        'update_id': _next_update_id(),
        'callback_query': {
            'id': str(_next_update_id()),
            'from': _make_user_dict(user_id),
            'chat_instance': str(chat_id),
            'data': data,
            'message': {
                'message_id': message_id,
                'from': {'id': 0, 'is_bot': True, 'first_name': 'Bot'},
                'chat': _make_chat_dict(chat_id),
                'date': int(time.time()),
                'text': '...',
            },
        },
    }
    return types.Update(**update_dict)
