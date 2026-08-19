"""Path confinement in the sandbox server: the shared skills dir is the only exception."""

import importlib.util
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = _load('sandbox_common', 'sandbox/server/common.py')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the module at a temporary /workspace and pretend a user is calling."""
    monkeypatch.setattr(common, 'WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(common, 'PUBLIC_SKILLS_DIR', str(tmp_path / 'public_skills'))
    (tmp_path / 'user_1' / 'skills').mkdir(parents=True)
    (tmp_path / 'public_skills' / 'skill-creator').mkdir(parents=True)
    (tmp_path / 'public_skills' / 'skill-creator' / 'SKILL.md').write_text('---\nname: x\n---\n')
    (tmp_path / 'user_2').mkdir()
    token = common.current_user.set('user_1')
    yield tmp_path
    common.current_user.reset(token)


def test_relative_path_resolves_inside_own_workspace(workspace):
    resolved, user = common.resolve_path('skills/mine/SKILL.md')
    assert resolved == str(workspace / 'user_1' / 'skills' / 'mine' / 'SKILL.md')
    assert user == 'user_1'


def test_public_skills_rejected_by_default(workspace):
    with pytest.raises(common.PathOutsideWorkspace):
        common.resolve_path(str(workspace / 'public_skills' / 'skill-creator' / 'SKILL.md'))


def test_public_skills_allowed_when_requested(workspace):
    path = str(workspace / 'public_skills' / 'skill-creator' / 'SKILL.md')
    resolved, user = common.resolve_path(path, allow_public=True)
    assert resolved == path
    assert user == 'user_1'


def test_other_users_workspace_stays_forbidden(workspace):
    with pytest.raises(common.PathOutsideWorkspace):
        common.resolve_path('../user_2/secret.txt', allow_public=True)


def test_traversal_out_of_the_volume_is_rejected(workspace):
    with pytest.raises(common.PathOutsideWorkspace):
        common.resolve_path('../../etc/passwd', allow_public=True)


def test_symlink_escape_is_rejected(workspace):
    os.symlink(workspace / 'user_2', workspace / 'user_1' / 'link')
    with pytest.raises(common.PathOutsideWorkspace):
        common.resolve_path('link/secret.txt', allow_public=True)


def test_personal_skills_dir_helper(workspace):
    assert common.personal_skills_dir('user_1') == str(workspace / 'user_1' / 'skills')
