#!/usr/bin/env python3
"""Validate a skill folder: frontmatter, naming, description, referenced files.

Usage: python3 validate_skill.py <path-to-skill-folder>

Exits 0 when the skill is valid (warnings do not fail), 1 when errors were found.
Stdlib only — it runs inside the sandbox, next to the skill it checks.
"""

import os
import re
import sys

# Keep in sync with SKILLS_MAX_DESCRIPTION_CHARS in the bot settings: longer
# descriptions are truncated in the catalog, which weakens the trigger.
MAX_DESCRIPTION_CHARS = 400
MAX_NAME_CHARS = 64
SKILL_MD_SOFT_LIMIT_BYTES = 12000

NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
REFERENCE_RE = re.compile(r'(?<![\w/.])((?:reference|scripts|templates)/[\w./-]+)')


def parse_frontmatter(text):
    """Returns (fields, error). Fields is a dict of the simple `key: value` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return None, 'SKILL.md must start with a "---" frontmatter line'
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            break
    else:
        return None, 'frontmatter is not closed with a "---" line'

    fields = {}
    for line in lines[1:i]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        fields[key.strip()] = value
    return fields, None


def validate(skill_dir):
    """Returns a list of (level, message) with level in {'error', 'warning'}."""
    problems = []
    skill_dir = skill_dir.rstrip('/')
    folder_name = os.path.basename(skill_dir)

    if not os.path.isdir(skill_dir):
        return [('error', f'not a directory: {skill_dir}')]

    skill_md = os.path.join(skill_dir, 'SKILL.md')
    if not os.path.isfile(skill_md):
        return [('error', f'missing {folder_name}/SKILL.md')]

    with open(skill_md, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()

    fields, error = parse_frontmatter(text)
    if error:
        return [('error', error)]

    name = fields.get('name', '')
    description = fields.get('description', '')

    if not name:
        problems.append(('error', 'frontmatter has no "name" field'))
    else:
        if name != folder_name:
            problems.append(
                ('error', f'name "{name}" does not match the folder name "{folder_name}"')
            )
        if not NAME_RE.match(name):
            problems.append(('error', f'name "{name}" must be kebab-case (a-z, 0-9, dashes)'))
        if len(name) > MAX_NAME_CHARS:
            problems.append(('error', f'name is {len(name)} chars, max is {MAX_NAME_CHARS}'))

    if not description:
        problems.append(('error', 'frontmatter has no "description" field'))
    else:
        if len(description) > MAX_DESCRIPTION_CHARS:
            problems.append((
                'error',
                f'description is {len(description)} chars, max is {MAX_DESCRIPTION_CHARS} '
                '(it gets truncated in the catalog)',
            ))
        if len(description) < 40:
            problems.append((
                'warning',
                'description is very short — say both what the skill does and when to use it',
            ))

    body = text.split('---', 2)[-1]
    for match in sorted(set(REFERENCE_RE.findall(body))):
        if not os.path.exists(os.path.join(skill_dir, match)):
            problems.append(('error', f'SKILL.md references {match}, which does not exist'))

    size = os.path.getsize(skill_md)
    if size > SKILL_MD_SOFT_LIMIT_BYTES:
        problems.append((
            'warning',
            f'SKILL.md is {size} bytes — move rarely needed detail into reference/',
        ))

    return problems


def main(argv):
    if len(argv) != 2:
        print('usage: validate_skill.py <path-to-skill-folder>')
        return 2

    skill_dir = argv[1]
    problems = validate(skill_dir)
    errors = [p for p in problems if p[0] == 'error']

    for level, message in problems:
        print(f'{level.upper()}: {message}')

    if errors:
        print(f'\n{len(errors)} error(s) — skill is not valid yet.')
        return 1
    print(f'OK: {os.path.basename(skill_dir.rstrip("/"))} is a valid skill.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
