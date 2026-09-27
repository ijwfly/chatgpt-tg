"""Unit tests for referring to images stored in the dialog context: labels and file_id recovery."""
import pytest

from app.openai_helpers.chatgpt import DialogMessage, DialogMessageContentPart, DialogMessageImageUrl
from app.runtime.image_refs import (
    collect_labeled_images, file_id_from_image_url, find_image, format_image_label, next_image_number,
)

PROXY = 'http://localhost:18321'


def _text(text):
    return DialogMessageContentPart(type='text', text=text)


def _image(file_id, tokens=1105):
    return DialogMessageContentPart(
        type='image_url', image_url=DialogMessageImageUrl(url=f'{PROXY}/{file_id}_{tokens}.jpg'),
    )


def _user_message(*parts):
    return DialogMessage(role='user', content=list(parts))


def _image_message(number, file_id):
    return _user_message(_text(format_image_label(number)), _image(file_id))


class TestFileIdFromImageUrl:
    def test_plain_file_id(self):
        assert file_id_from_image_url(f'{PROXY}/AgACAgIAAx0_1105.jpg') == 'AgACAgIAAx0'

    def test_file_id_with_underscores_and_dashes(self):
        file_id = 'AgACAgIAAx0CAAM-Bd_x9QAB_hello-world'
        assert file_id_from_image_url(f'{PROXY}/{file_id}_255.jpg') == file_id

    def test_url_without_token_suffix_is_not_an_image_ref(self):
        assert file_id_from_image_url(f'{PROXY}/somefile.jpg') is None

    def test_empty_url(self):
        assert file_id_from_image_url('') is None


class TestNextImageNumber:
    def test_starts_at_one(self):
        assert next_image_number([]) == 1
        assert next_image_number([DialogMessage(role='user', content='hello')]) == 1

    def test_continues_after_existing_labels(self):
        messages = [_image_message(1, 'file-a'), _image_message(2, 'file-b')]
        assert next_image_number(messages) == 3

    def test_uses_the_highest_label_not_the_count(self):
        """Summarization can drop earlier messages; numbering must not go backwards."""
        messages = [_image_message(7, 'file-a')]
        assert next_image_number(messages) == 8

    def test_finds_labels_in_plain_text_messages(self):
        messages = [DialogMessage(role='user', content='see [image #4] above')]
        assert next_image_number(messages) == 5


class TestCollectLabeledImages:
    def test_collects_in_dialog_order(self):
        messages = [_image_message(1, 'file-a'), DialogMessage(role='assistant', content='ok'),
                    _image_message(2, 'file-b')]
        assert collect_labeled_images(messages) == [(1, 'file-a'), (2, 'file-b')]

    def test_several_images_in_one_message(self):
        message = _user_message(
            _text('two photos'),
            _text(format_image_label(1)), _image('file-a'),
            _text(format_image_label(2)), _image('file-b'),
        )
        assert collect_labeled_images([message]) == [(1, 'file-a'), (2, 'file-b')]

    def test_image_without_a_label_has_no_number(self):
        """Dialogs recorded before labels existed still expose their images."""
        messages = [_user_message(_text('look'), _image('file-old'))]
        assert collect_labeled_images(messages) == [(None, 'file-old')]

    def test_ignores_messages_without_content_parts(self):
        messages = [DialogMessage(role='user', content='just text'), DialogMessage(role='assistant')]
        assert collect_labeled_images(messages) == []


class TestFindImage:
    def test_defaults_to_the_most_recent_image(self):
        messages = [_image_message(1, 'file-a'), _image_message(2, 'file-b')]
        assert find_image(messages) == (2, 'file-b')

    def test_finds_an_earlier_image_by_its_label(self):
        messages = [_image_message(1, 'file-a'), _image_message(2, 'file-b')]
        assert find_image(messages, 1) == (1, 'file-a')

    def test_unknown_label(self):
        assert find_image([_image_message(1, 'file-a')], 5) is None

    def test_no_images_at_all(self):
        assert find_image([DialogMessage(role='user', content='hi')]) is None


@pytest.mark.parametrize('number,expected', [(1, '[image #1]'), (12, '[image #12]')])
def test_format_image_label(number, expected):
    assert format_image_label(number) == expected
