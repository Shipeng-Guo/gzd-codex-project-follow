# Codex Local Project Follow

A Windows Codex skill for restoring missing local sidebar projects and their chat assignments when switching between accounts in the same Windows user profile. Start with a preview. Choose a one-time repair or enable the optional background worker when you want all accounts in that profile to share this metadata.

## Try it

Download the source and copy the skill folder to `~/.codex/skills/gzd-codex-project-follow` (the default Windows layout). In PowerShell, open that directory and run:

```powershell
.\scripts\manage.ps1 -Action Preview
```

Preview shows pending additions and conflicts. Installing the skill files alone does not start a service. See [the skill guide](SKILL.md) for ongoing use and [operations](references/operations.md) for the one-time command, compatibility, backups and rollback.

The worker uses internal data from the packaged Windows desktop app. It synchronizes missing entries after a full app exit. Accounts whose project lists are already populated can be used normally.

## Verify the source

The isolated tests use synthetic accounts and temporary folders. Run them from the skill directory with Python 3.9 or newer:

```powershell
python -B .\scripts\test_project_follow.py
```

Keep local catalogs, diagnostics and full-state backups on the device. For a public issue, provide the app build, the worker phase, and counts or conflict categories. See [runtime data and privacy](references/operations.md#runtime-data-and-privacy).
