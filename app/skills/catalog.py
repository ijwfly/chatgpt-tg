"""Skills catalog: the only part of a skill that lives in the system prompt.

A skill is a folder in the sandbox (personal `skills/` in the user's workspace, or shared
read-only `/workspace/public_skills`) with a SKILL.md whose frontmatter carries `name` and
`description`. Only those two fields plus the path reach the prompt — the agent reads the
body itself with read_file when a description matches the task at hand.
"""

import logging

import settings
from app.sandbox.client import SandboxClient, SandboxError

logger = logging.getLogger(__name__)

HEADER = """## Skills
Skills are folders with instructions for a specific kind of task. If a skill's description \
matches the task at hand, read its SKILL.md first and follow it — it is written for exactly \
this situation and overrides your default approach. Personal skills live in `skills/` in your \
workspace and can be created and edited; skills under {public_dir} are shared and read-only. \
A skill you create appears in this list starting with the next user message.

Available skills:"""


def _clean(text: str, limit: int) -> str:
    text = ' '.join((text or '').split())
    if len(text) > limit:
        text = text[:limit].rstrip() + '...'
    return text


def _skill_path(skill: dict, personal_dir: str) -> str:
    """Path the agent can pass to read_file: relative for personal, absolute for shared."""
    skill_md = skill.get('skill_md') or ''
    if skill.get('scope') == 'personal' and personal_dir and skill_md.startswith(personal_dir):
        return 'skills' + skill_md[len(personal_dir):]
    return skill_md


def format_catalog(catalog: dict) -> str:
    """Render the prompt block. Returns '' when there is nothing to list."""
    personal_dir = catalog.get('personal_dir') or ''
    public_dir = catalog.get('public_dir') or '/workspace/public_skills'

    seen = set()
    lines = []
    # personal skills come first from the sandbox, so a personal skill shadows a shared
    # one with the same name
    for skill in catalog.get('skills') or []:
        name = _clean(skill.get('name', ''), 64)
        description = _clean(skill.get('description', ''), settings.SKILLS_MAX_DESCRIPTION_CHARS)
        if not name or not description or name in seen:
            continue
        seen.add(name)
        scope = 'personal' if skill.get('scope') == 'personal' else 'shared'
        lines.append(f'- {name} ({scope}): {description} -> {_skill_path(skill, personal_dir)}')
        if len(lines) >= settings.SKILLS_MAX_COUNT:
            break

    if not lines:
        return ''
    return HEADER.format(public_dir=public_dir) + '\n' + '\n'.join(lines)


async def get_skills_prompt_addition(user) -> str:
    """Skills catalog block for the agent system prompt, '' when unavailable or empty."""
    if not (settings.ENABLE_SKILLS and settings.ENABLE_BASH_SANDBOX):
        return ''
    try:
        catalog = await SandboxClient().list_skills(user.telegram_id)
    except SandboxError as e:
        logger.warning(f'Skills catalog unavailable: {e}')
        return ''
    except Exception as e:
        logger.error(f'Error loading skills catalog: {e}')
        return ''

    invalid = catalog.get('invalid') or []
    if invalid:
        logger.warning(f'Skipped invalid skills for user {user.telegram_id}: {invalid}')
    return format_catalog(catalog)
