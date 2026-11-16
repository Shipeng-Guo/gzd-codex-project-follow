---
name: gzd-codex-project-follow
description: Inspect or restore missing Codex desktop sidebar projects and local chat assignments after switching accounts within one Windows user profile. Use for preview, optional background synchronization, exclusions, or rollback.
---

# Codex Local Project Follow

Use this skill when a local project or its chat grouping appears in one Codex account and is missing in another account owned or managed by the same person on the same Windows profile. The follower copies project registrations and local chat-to-project assignments into **every account snapshot it finds in that profile**, including accounts encountered later. Use it only when the user intends to share that metadata across the full set. It works with the folders and chats already on the computer.

## Check what is missing

Run these commands in PowerShell from this skill's directory.

```powershell
.\scripts\manage.ps1 -Action Status
.\scripts\manage.ps1 -Action Preview
```

Check the reported phase, process identity (`WorkerRunning`), and whether the catalog is paused. Preview calculates the changes from the current state and catalog without applying them. If the desired items are already visible, normal account switching is sufficient. For troubleshooting, keep raw diagnostics on the device and share counts or conflict types. See [storage and compatibility](references/operations.md) for supported fields and what the output can contain.

## Restore or enable automatic follow

For a one-time repair, use the standalone `sync` command described in [operations](references/operations.md#one-time-repair). For ongoing synchronization, run `.\scripts\manage.ps1 -Action Install` when the user has chosen to enable a background worker and Windows login startup. Keep the installed skill in a stable directory. Verify the worker with `Status`.

While Codex is open, the worker records observed additions locally. It writes missing entries after the app exits and the worker obtains the profile lock. When a newly used account still shows missing items, finish signing in, fully exit Codex, let the worker synchronize, and reopen it. Existing account snapshots can be filled in advance during a normal exit.

The follower keeps each target's existing values on conflicts. A removal after that account's baseline was recorded suppresses reimport into that account. Adding the item back through Codex clears that suppression. An explicit project exclusion stops future imports across accounts.

## Manage an installation

| Action | Use |
| --- | --- |
| `Start`, `Stop` | Start the worker or stop it gracefully for this session. |
| `Pause`, `Resume` | Persist the synchronization preference across worker restarts. |
| `Exclude -Project <id>`, `Include -Project <id>` | Change future handling of a cataloged project by its legacy project ID. |
| `Backups` | List local transaction records. |
| `Rollback -Backup <id>` | Restore matching fields from a completed transaction and pause synchronization. Run after exiting Codex. |
| `Uninstall` | Stop the worker and remove Windows login startup. The skill and private recovery files stay on disk. |
