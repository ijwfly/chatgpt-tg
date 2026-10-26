---
name: skill-creator
description: Create, edit, or install skills from a ZIP archive or folder. Use when asked to make a skill, save a workflow as a skill, install an uploaded skill, fix a skill installed in the wrong place, or fix a skill that is not triggering.
---

# Creating and installing skills

A skill is a folder of instructions you load only when the task needs it. Its `description`
is the only trigger — the catalog in your system prompt lists names and descriptions, and you
decide from there whether to open the body.

For an existing skill supplied as a ZIP or folder, follow **Install an existing skill** below;
do not start the creation interview. For a new skill, follow steps 1–6.

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

Personal skills must live in `skills/<skill-name>/` inside your persistent home/workspace
to be discovered. You can write elsewhere in the workspace, but a skill placed there is
not installed.
Keep task work in `projects/<task-name>/`, deliverables in `outbox/<task-name>/`, and archive
staging in `tmp/<task-name>/`, not directly in `~/`. User uploads are in `inbox/`; preserve
the originals unless asked to remove them. For an uploaded skill, its archive in `inbox/`
is not an installation: the final path must be `skills/<skill-name>/SKILL.md`, without
extra wrapper directories.
`/workspace/public_skills/` is shared and read-only; to adapt a shared skill, copy it
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

Also check that the final path is `skills/<skill-name>/SKILL.md`, relative to the workspace,
with no extra wrapper directory. The validator checks contents, not installation location;
the catalog only scans immediate subfolders of `skills/`.

## 6. Hand it over

Show the user the skill's name, description and file tree, and say plainly that the catalog
picks it up starting with their next message. Suggest a test phrasing that should trigger it —
if it does not trigger, the description is the thing to fix, not the body.

## Install an existing skill

An uploaded ZIP is saved to `inbox/` in the persistent home/workspace. That is the upload
location, **not** the installation location. Install only when the user requests it;
not every ZIP is a skill.

1. **Inspect before extracting.** List archive entries with Python's stdlib `zipfile` (do not
   assume `unzip` is installed). Reject absolute paths, `..` traversal, symlinks, and any
   entry whose resolved destination escapes the extraction directory. Do not execute scripts
   from the uploaded skill to install it.
2. **Stage inside the workspace.** Extract into a new temporary directory under `tmp/<task-name>/`,
   never directly into the workspace root or over existing skills. For an existing folder,
   inspect its contents and reject links that escape the folder before copying it.
3. **Find the skill root.** Locate `SKILL.md`: it may be at the archive root, inside a named
   folder, or inside an extra wrapper folder. Ignore packaging metadata such as `__MACOSX`.
   If there is no `SKILL.md`, report that the input is not a skill. If there are several,
   install all only if requested; otherwise ask which skills the user wants.
4. **Read and validate.** Read the frontmatter and use its `name`, not the ZIP filename, as the
   destination folder name. It must be kebab-case and match the final folder name. Preserve
   the entire skill folder, including companion files and their relative paths; do not copy
   just `SKILL.md` or rewrite the skill's instructions. Validate the staged folder with the
   bundled validator from step 5 (stage it under its declared name). Report invalid content
   rather than silently changing an externally authored skill.
5. **Check the destination.** The only personal installation path is `skills/<skill-name>/`.
   If it already exists, do not merge or overwrite it unless the user explicitly requested
   replacement of that skill. Validate the replacement before touching the installed copy;
   replace the whole folder so obsolete files do not survive. A shared skill with the same
   name will be shadowed by this personal copy; tell the user.
6. **Install and verify.** Create `skills/` if needed and copy or move the complete staged
   skill folder to `skills/<skill-name>/`. There must be exactly one skill-folder level
   between `skills/` and `SKILL.md`, not `skills/<wrapper>/<skill-name>/SKILL.md`.
   Run the bundled validator again against the final path:

   ```
   python3 /workspace/public_skills/skill-creator/scripts/validate_skill.py skills/<skill-name>
   ```

   Check the final path separately: passing validation outside `skills/` does not mean the
   skill is installed. Remove temporary staging files you created, but keep the uploaded
   archive or original folder unless the user asked to remove it.
7. **Confirm the result.** Show the installed name, description, final path and file tree.
   Explain that it enters the catalog on the next user message, not during this turn.
   Suggest a request that should trigger it.

To fix a skill previously unpacked into the workspace root, follow the same inspection,
validation and conflict checks, then move its complete folder into `skills/<skill-name>/`.
Do not leave a second active copy or delete unrelated workspace files.
