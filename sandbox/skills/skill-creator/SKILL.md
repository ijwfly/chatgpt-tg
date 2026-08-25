---
name: skill-creator
description: Creating a new skill or editing an existing one. Use when asked to make a skill, save a workflow or process as a skill, teach you to do something the same way every time, or fix a skill that is not triggering.
---

# Creating a skill

A skill is a folder of instructions you load only when the task needs it. Its `description`
is the only trigger — the catalog in your system prompt lists names and descriptions, and you
decide from there whether to open the body.

## 1. Find out what the skill is for

Ask the user, in one batched message, whatever you cannot infer:

- What task should the skill cover, end to end?
- **How does the user normally phrase the request?** These phrasings go into the description.
- Any conventions that must be followed: formats, file layout, tools, output style, gotchas.
- Is there a repeatable procedure that should be a script instead of prose?

Do not invent requirements. If the user already described the process (for example you just
did the task together), summarise it back and ask only what is missing.

## 2. Decide the layout

```
skills/<skill-name>/
  SKILL.md        required
  reference/      optional: long lookup material, read only when a step needs it
  scripts/        optional: executable helpers, run via bash_exec
  templates/      optional: examples and boilerplate
```

Rules of thumb:

- `SKILL.md` stays short — the steps, not the encyclopedia. Anything long, rarely needed, or
  lookup-shaped goes to `reference/` and is mentioned by relative path.
- A deterministic procedure belongs in `scripts/`: running it costs only its stdout, while
  regenerating the same code every time costs tokens and varies.
- Scripts must be non-interactive, print a clear result, and exit non-zero on failure.
- Do not write down what you already know. The skill carries what is specific to this task
  and this environment: conventions, order of steps, result format, traps, example calls.

## 3. Write the description

Answer two questions: **what it does** and **when to apply it**, using the words the user
actually says.

- Bad: `Helps with reports.`
- Good: `Builds a weekly spending summary from a CSV bank statement; use when a statement is
  sent and a report, summary or spending analysis is requested.`

Keep it one or two sentences. It must fit the catalog limit, so stay well under 400 characters.

## 4. Create the files

Personal skills live in `skills/` inside your workspace — that is the only place you can
write. `/workspace/public_skills/` is shared and read-only; to adapt a shared skill, copy it
into `skills/` under the same name (a personal skill overrides a shared one of the same name).

Write `SKILL.md` with `write_file`:

```
---
name: <folder-name>
description: <what it does and when to use it>
---

<imperative, step-by-step instructions; refer to companion files by relative path,
e.g. "see reference/<file>.md", "run scripts/<file>.py">
```

The folder name and the `name` field must match, kebab-case.

## 5. Validate

```
python3 /workspace/public_skills/skill-creator/scripts/validate_skill.py skills/<skill-name>
```

It checks the frontmatter, the name, the description length and that every relative file the
body references exists. Fix whatever it reports and run it again until it passes.

## 6. Hand it over

Show the user the skill's name, description and file tree, and say plainly that the catalog
picks it up starting with their next message. Suggest a test phrasing that should trigger it —
if it does not trigger, the description is the thing to fix, not the body.
