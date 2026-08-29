"""Unit tests for the rich message helpers: markdown splitting and escaping."""
from app.bot.rich_messages import escape_rich_markdown, split_markdown


def _reassemble(parts):
    return '\n'.join(parts)


class TestSplitMarkdown:

    def test_short_content_is_untouched(self):
        assert split_markdown('hello', max_len=100) == ['hello']

    def test_splits_at_newline_first(self):
        content = 'line one\nline two\nline three'
        parts = split_markdown(content, max_len=20)
        assert parts == ['line one', 'line two\nline three']
        assert all(len(p) <= 20 for p in parts)

    def test_falls_back_to_sentence_then_space(self):
        content = 'First sentence. Second sentence that is long enough to be cut somewhere in the middle'
        parts = split_markdown(content, max_len=40)
        assert parts[0] == 'First sentence'
        assert all(len(p) <= 40 for p in parts)
        assert all(word in ' '.join(parts) for word in content.replace('.', '').split())

    def test_hard_cut_without_separators(self):
        content = 'x' * 50
        parts = split_markdown(content, max_len=20)
        assert ''.join(parts) == content
        assert all(len(p) <= 20 for p in parts)

    def test_code_fence_is_closed_and_reopened_at_the_cut(self):
        code_lines = '\n'.join(f'print({i})' for i in range(30))
        content = f'Intro\n```python\n{code_lines}\n```\nOutro'
        parts = split_markdown(content, max_len=120)

        assert len(parts) > 1
        for part in parts:
            assert len(part) <= 120
            # every part has balanced fences
            assert part.count('```') % 2 == 0, part
        # the continuation re-opens the fence with the language
        assert parts[1].startswith('```python\n')
        assert parts[0].endswith('```')
        assert parts[-1].endswith('Outro')
        # no code line is lost
        for i in range(30):
            assert f'print({i})' in _reassemble(parts)

    def test_content_outside_fences_gets_no_extra_fences(self):
        content = '```\ncode\n```\n' + '\n'.join(f'paragraph {i}' for i in range(40))
        parts = split_markdown(content, max_len=60)
        assert sum(p.count('```') for p in parts) == 2


class TestEscapeRichMarkdown:

    def test_escapes_markdown_specials_and_html(self):
        assert escape_rich_markdown('a*b_c`d[e]f#g~h=i|j!k') == 'a\\*b\\_c\\`d\\[e\\]f\\#g\\~h\\=i\\|j\\!k'
        assert escape_rich_markdown('<script>') == '&lt;script&gt;'
        assert escape_rich_markdown('back\\slash') == 'back\\\\slash'

    def test_plain_text_is_untouched(self):
        assert escape_rich_markdown('John Smith') == 'John Smith'
