"""Unit tests for the sandbox-side skills code.

Both modules are stdlib-only and live outside the app package (they run inside the sandbox
container), so they are loaded by path. The e2e suite mocks the sandbox away, which makes
these the only tests covering the scan and the validator.
"""

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(name, relative_path):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


file_helper = _load('sandbox_file_helper', 'sandbox/server/file_helper.py')
validate_skill = _load(
    'validate_skill', 'sandbox/skills/skill-creator/scripts/validate_skill.py'
)


VALID_SKILL_MD = """---
name: weekly-report
description: Builds a weekly spending summary from a CSV bank statement; use when a statement is sent and a report is requested.
---

Group the rows by week and see reference/format.md for the output layout.
"""


def _write_skill(root, name, text, extra_files=()):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / 'SKILL.md').write_text(text)
    for rel in extra_files:
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('x')
    return skill_dir


class TestFrontmatterParsing:

    def test_parses_name_and_description(self):
        fields = file_helper.parse_frontmatter(VALID_SKILL_MD)
        assert fields['name'] == 'weekly-report'
        assert fields['description'].startswith('Builds a weekly spending summary')

    def test_strips_quotes(self):
        fields = file_helper.parse_frontmatter('---\nname: "quoted"\ndescription: \'single\'\n---\nbody')
        assert fields['name'] == 'quoted'
        assert fields['description'] == 'single'

    def test_missing_frontmatter_raises(self):
        with pytest.raises(ValueError):
            file_helper.parse_frontmatter('# Just a heading\n')

    def test_unterminated_frontmatter_raises(self):
        with pytest.raises(ValueError):
            file_helper.parse_frontmatter('---\nname: x\ndescription: y\n')


class TestScanSkillsDir:

    def test_scans_valid_skills(self, tmp_path):
        _write_skill(tmp_path, 'weekly-report', VALID_SKILL_MD, ['reference/format.md'])
        skills, invalid = file_helper.scan_skills_dir(str(tmp_path), 'personal')

        assert invalid == []
        assert len(skills) == 1
        assert skills[0]['name'] == 'weekly-report'
        assert skills[0]['scope'] == 'personal'
        assert skills[0]['skill_md'].endswith('weekly-report/SKILL.md')

    def test_missing_root_is_not_an_error(self, tmp_path):
        skills, invalid = file_helper.scan_skills_dir(str(tmp_path / 'nope'), 'personal')
        assert (skills, invalid) == ([], [])

    def test_broken_skills_are_reported_not_raised(self, tmp_path):
        _write_skill(tmp_path, 'no-frontmatter', 'just text\n')
        _write_skill(tmp_path, 'no-description', '---\nname: no-description\n---\nbody\n')
        (tmp_path / 'empty-dir').mkdir()
        (tmp_path / '.hidden').mkdir()

        skills, invalid = file_helper.scan_skills_dir(str(tmp_path), 'public')

        assert skills == []
        reasons = {item['dir']: item['error'] for item in invalid}
        assert 'frontmatter' in reasons['no-frontmatter']
        assert reasons['no-description'] == 'no description'
        assert reasons['empty-dir'] == 'no SKILL.md'
        assert '.hidden' not in reasons

    def test_body_is_not_returned(self, tmp_path):
        _write_skill(tmp_path, 'weekly-report', VALID_SKILL_MD, ['reference/format.md'])
        skills, _ = file_helper.scan_skills_dir(str(tmp_path), 'personal')
        assert 'Group the rows by week' not in str(skills[0])


class TestValidateSkill:

    def _errors(self, skill_dir):
        return [message for level, message in validate_skill.validate(str(skill_dir))
                if level == 'error']

    def test_valid_skill_passes(self, tmp_path):
        skill_dir = _write_skill(tmp_path, 'weekly-report', VALID_SKILL_MD, ['reference/format.md'])
        assert self._errors(skill_dir) == []
        assert validate_skill.main(['validate_skill.py', str(skill_dir)]) == 0

    def test_name_must_match_folder(self, tmp_path):
        skill_dir = _write_skill(tmp_path, 'other-name', VALID_SKILL_MD, ['reference/format.md'])
        assert any('does not match the folder name' in e for e in self._errors(skill_dir))

    def test_missing_referenced_file(self, tmp_path):
        skill_dir = _write_skill(tmp_path, 'weekly-report', VALID_SKILL_MD)
        assert any('reference/format.md' in e for e in self._errors(skill_dir))

    def test_missing_skill_md(self, tmp_path):
        (tmp_path / 'empty-skill').mkdir()
        assert any('missing' in e for e in self._errors(tmp_path / 'empty-skill'))

    def test_description_too_long(self, tmp_path):
        text = VALID_SKILL_MD.replace(
            'Builds a weekly spending summary from a CSV bank statement; use when a statement is sent and a report is requested.',
            'y' * (validate_skill.MAX_DESCRIPTION_CHARS + 1),
        )
        skill_dir = _write_skill(tmp_path, 'weekly-report', text, ['reference/format.md'])
        assert any('description is' in e for e in self._errors(skill_dir))

    def test_name_must_be_kebab_case(self, tmp_path):
        text = VALID_SKILL_MD.replace('name: weekly-report', 'name: Weekly_Report')
        skill_dir = _write_skill(tmp_path, 'Weekly_Report', text, ['reference/format.md'])
        assert any('kebab-case' in e for e in self._errors(skill_dir))

    def test_bundled_skill_creator_is_valid(self):
        skill_dir = REPO_ROOT / 'sandbox' / 'skills' / 'skill-creator'
        assert self._errors(skill_dir) == []
