from contextlib import contextmanager
from unittest.mock import patch

from app.sandbox.client import SandboxError

SANDBOX_CLIENT_IMPORT_SITES = (
    'app.functions.bash_sandbox.SandboxClient',
    'app.bot.batched_input_handler.SandboxClient',
    'app.skills.catalog.SandboxClient',
)


class FakeSandboxClient:
    """In-memory fake of app.sandbox.client.SandboxClient."""
    uploads = {}
    files = {}
    exec_results = []
    exec_calls = []
    download_result = None
    skills_result = None
    skills_calls = []

    def __init__(self, base_url=None):
        pass

    @classmethod
    def reset(cls):
        cls.uploads = {}
        cls.files = {}
        cls.exec_results = []
        cls.exec_calls = []
        cls.download_result = None
        cls.skills_result = None
        cls.skills_calls = []

    async def exec(self, telegram_user_id, command, timeout):
        FakeSandboxClient.exec_calls.append({'user': telegram_user_id, 'command': command})
        if FakeSandboxClient.exec_results:
            return FakeSandboxClient.exec_results.pop(0)
        return {'stdout': '', 'stderr': '', 'exit_code': 0, 'cwd': '/workspace/user_test'}

    async def stat(self, telegram_user_id, path):
        if path in FakeSandboxClient.uploads:
            return {'type': 'file', 'size': len(FakeSandboxClient.uploads[path])}
        return {'type': 'missing'}

    async def read_file(self, telegram_user_id, path, limit=0):
        if path not in FakeSandboxClient.files:
            raise SandboxError(f'File not found: {path}')
        return {'content': FakeSandboxClient.files[path]}

    async def write_file(self, telegram_user_id, path, content):
        FakeSandboxClient.files[path] = content
        return {'status': 'ok', 'size': len(content), 'path': path}

    async def upload_file(self, telegram_user_id, rel_path, data):
        FakeSandboxClient.uploads[rel_path] = data
        return {'status': 'ok', 'size': len(data), 'path': rel_path}

    async def download_file(self, telegram_user_id, rel_path, max_bytes):
        if FakeSandboxClient.download_result is None:
            raise SandboxError(f'Not found: {rel_path}')
        return FakeSandboxClient.download_result

    async def list_skills(self, telegram_user_id):
        FakeSandboxClient.skills_calls.append(telegram_user_id)
        if FakeSandboxClient.skills_result is None:
            return make_catalog()
        return FakeSandboxClient.skills_result


def make_catalog(skills=(), invalid=(), telegram_id='test'):
    return {
        'personal_dir': f'/workspace/user_{telegram_id}/skills',
        'public_dir': '/workspace/public_skills',
        'skills': list(skills),
        'invalid': list(invalid),
    }


def make_skill(name, description, scope='personal', telegram_id='test'):
    if scope == 'personal':
        skill_dir = f'/workspace/user_{telegram_id}/skills/{name}'
    else:
        skill_dir = f'/workspace/public_skills/{name}'
    return {
        'name': name,
        'description': description,
        'scope': scope,
        'dir': skill_dir,
        'skill_md': f'{skill_dir}/SKILL.md',
    }


@contextmanager
def patch_sandbox_client(client_cls=FakeSandboxClient):
    """Patch every SandboxClient import site with the fake."""
    client_cls.reset()
    with patch(SANDBOX_CLIENT_IMPORT_SITES[0], client_cls), \
            patch(SANDBOX_CLIENT_IMPORT_SITES[1], client_cls), \
            patch(SANDBOX_CLIENT_IMPORT_SITES[2], client_cls):
        yield client_cls
