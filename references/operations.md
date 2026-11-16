# Operations

## Compatibility

The manager targets the packaged Windows Codex desktop app with its default per-user state directory, Windows PowerShell with AppX and CIM support, and the Codex Primary Runtime containing both `python.exe` and `pythonw.exe`. It resolves the runtime at launch. The profile lock path in the Python CLI is specific to that app package. A custom data directory, another app package, or a different operating system requires separate verification.

The known storage layout was tested with desktop build `26.1103.7773.0`. The follower relies on internal application data. It validates the known JSON shapes before writing. After an app update, inspect any schema error and verify the storage layout before attempting a repair.

## One-time repair

Use an independent PowerShell window opened in the skill directory. Preview while Codex is open. If a worker is already running, use `.\scripts\manage.ps1 -Action Stop` and let it finish. A paused catalog also pauses the one-time command. Fully exit Codex and any Codex CLI sessions, then run these commands with Python 3.9 or newer on Windows (`python` below can be replaced with the path to the packaged interpreter).

```powershell
python .\scripts\project_follow.py sync
.\scripts\manage.ps1 -Action Status
```

Look for `synchronized`, then reopen Codex and inspect the sidebar. A one-time sync can complete without a running worker or a Windows startup entry. If a worker was running before the repair, use `Start` to restore that choice.

## State and merge rules

The application stores the active owner's globals in `~/.codex/.codex-global-state.json` and inactive globals in `electron-account-renderer-state-v1.inactive[scope].globals`. The worker uses the opaque scope keys already in that file. It ignores unowned top-level globals while signed out.

| Field | Merge behavior |
| --- | --- |
| `local-projects` | Add missing registrations. Preserve target metadata and report path or identity conflicts. |
| `thread-project-assignments` | Add missing local project assignments. Honor explicit projectless chats. |
| `thread-project-membership-host-ids` | Add missing host metadata for matching assignments. |
| `project-order` | Append newly imported projects to the target's existing order. |
| `app-server-project-id-by-legacy-project-id-by-host` | Add missing mappings for eligible projects on local hosts. |
| `app-server-projects-migration-by-host` | Update version 1 migration checkpoints for newly imported projects and memberships. |

The first observation establishes an account's baseline. Later removals are evaluated against that account's own baseline after the app exits. A local reassignment can supply the value for a missing entry on another account. Existing target assignments take precedence. Project files, chat transcripts and cloud history are outside the merge.

## Runtime data and privacy

The private catalog under `~/.codex/project-follow/` contains account scope keys, project names and folder paths, chat identifiers and their assignments. Each write transaction saves complete before and after copies of application state plus catalog recovery data in `backups/`. These files can contain unrelated desktop settings and data from other accounts on the profile.

`Status`, `Preview` and `Backups` are local diagnostics. Conflict details can contain a shortened account key and full project or chat IDs. Error text can contain local paths. For a public issue, share the phase, counts and conflict categories. Keep raw output, catalogs, backups and application state outside the public repository. The worker uses the local filesystem and Windows APIs and requires no network service.

## Background operation and recovery

`Install` registers a current-user Windows login startup value named `GzdCodexProjectFollow`. The manager launches a hidden worker with WMI. The worker holds a single-instance lock and polls the state file. Before changing app state it checks for `ChatGPT.exe` and `codex.exe`, waits for them to exit, and takes exclusive ownership of the app profile lock.

The worker saves the prewrite state, checks that its inputs still match, and atomically replaces each state file. If a transaction is interrupted, it attempts recovery on the next safe exit. A newer external change causes recovery to stop for review. A `deferred-error` phase or a pending recovery calls for inspecting the local message and the current app build.

To roll back, list `Backups` and select a committed transaction. Exit Codex and run `.\scripts\manage.ps1 -Action Rollback -Backup <id>` in an independent PowerShell window. The manager checks each account's six managed fields against the saved result, restores their previous values when they match, records a new transaction and pauses future synchronization. `Resume` re-enables it. `Uninstall` removes the worker's startup registration and retains the local recovery data.
