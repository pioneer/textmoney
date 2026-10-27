# Data safety, backups and recovery

This is the current local-save contract. Keep independent backups and use one
writer at a time: finish editing and wait for the external synchronization tool
before opening another device. TxnCount does not synchronize, merge journals or
certify cloud completion. See [Sequential editing](synchronization.md).

## Validation and conflicts

Before editing, the lossless parser must round-trip the source exactly. Candidate
syntax and the supported accounting model are validated before storage changes.
Only selected directives change; unrelated text and line endings are preserved.
See [Supported journal format](journal-format.md).

Every edit/confirmation captures its source baseline and journal object.
Polling, reload or a folder change cannot silently apply an old form to a newer
version. A detected conflict refuses the stale save and retains the draft.
Review current data, cancel/reopen the edit and submit explicitly. Drafts are
memory-only and do not survive app termination.

The save pipeline compares SHA-256 hashes, creates and read-verifies the previous
contents as a unique backup, rechecks the baseline, writes the validated candidate
and compares read-back bytes before publishing the new in-memory ledger.
A failed backup prevents modification. An uncertain write may already have saved
some or all bytes: inspect the journal and backup before reloading or retrying,
to avoid duplicate submission. There is no automatic restoration or retry.

Neither adapter provides cross-process locking or compare-and-swap. Another
writer can still change the journal after the final check. No `.txncount-sync/`
directory is created, read or required; legacy contents are ignored and never
automatically deleted. These protections do not detect every rollback while
the app was closed.

## Backup retention

After a new journal has been written and read back successfully, automatic
cleanup (enabled by default) keeps the union of:

- The 20 most recent completed-write snapshots.
- The latest snapshot on each of the last 30 UTC calendar dates, including today.
- The latest snapshot in each of the last 12 UTC calendar months, including this month.

Overlapping tiers retain one file, not duplicates. Recent snapshots can remain
even outside the daily/monthly windows. Future-dated snapshots and the current
session's latest recovery copy are protected. Retention bounds eligible history,
not total bytes or protected files; each snapshot is a full journal copy.

**Settings → Backups** offers a device-local automatic-cleanup setting and
**Clean up old backups**. Manual cleanup previews eligible count/bytes and requires
confirmation. Confirmation recomputes retention and cannot expand the approved
deletion set. Cleanup verifies the current baseline and never rewrites the journal.
It is not an atomic snapshot of the directory.

New names start as `txncount-<timestamp>-<uuid>-pending.beancount` and become
`-committed.beancount` only after successful journal verification. Failed,
conflicting, cancelled or uncertain writes leave protected pending recovery
copies, including across restart. Explicit diagnostic/manual backups are also
protected. Finalization or pruning failures after a verified save produce a
saved-with-maintenance-warning result, not a financial retry. Pruning can fail
after deleting some eligible files.

UUID-only legacy backups are not migrated or deleted: their creation time and
failed-write status cannot be established reliably. Unknown filenames, pending
copies, directories and symlinks are not deletion candidates. Review and archive
protected copies externally only after taking an independent copy and confirming
the journal is correct.

Desktop uses checked filesystem operations. Android lists, renames and deletes
through SAF. Providers that cannot perform maintenance report errors rather than
guessing. Synchronization tools propagate deletions; pause cleanup while
troubleshooting synchronization. Retention is not independent backup or undo.

## Desktop atomic replacement

Linux and Windows use this sequence:

1. Validate the candidate, check the baseline, create/read-verify the previous
   backup and check again.
2. Exclusively create an unpredictable same-directory
   `.<journal-name>.<random>.tmp`; do not truncate the original.
3. Preserve existing permissions, write the candidate, flush, apply mode bits
   and fsync the temporary file; then close it.
4. Recheck the original hash and target restrictions, then call `os.replace()`.
   Replacement is the commit point.
5. Fsync the parent directory where supported. Windows skips directory sync with
   an explicit durability warning. Other unsupported filesystems also warn;
   unexpected errors after replacement are surfaced as post-commit failures.
6. Read back the journal and compare exact bytes before updating the baseline,
   finalizing the backup and applying retention.

On filesystems supporting same-directory atomic replacement, newly opened readers
see complete old or new contents; existing handles may still see the old file.
Staging, permission-copy or file-fsync failures leave the original untouched.
Ordinary pre-replacement failures/conflicts remove the temporary file; cleanup
failures are reported. This is not a guarantee against adversarial directory races.

Existing POSIX ownership/mode, Linux access ACLs and Windows DACLs are preserved.
Permission-copy failure aborts before replacement. New POSIX files are `0600`;
new Windows files inherit folder permissions. Inode identity, timestamps,
arbitrary extended attributes, Windows ownership/SACLs and alternate streams are
not preserved. Folder write permission and Windows sharing rules can prevent
replacement; there is no truncating fallback.

Fsync improves durability but does not certify every disk, network filesystem or
sync provider. Backup creation/finalization does not fsync the backup directory.
Directory-sync/read-back errors can occur after commit: readiness is invalidated
and the pending backup is retained. An error from replacement itself is treated
as uncertain; any remaining complete temporary file and backup are kept.
Crashes can leave hidden staging files, which are not automatically promoted or
pruned. Cancelling an async caller does not stop an already running I/O thread.

Windows CI exercises tests and installer/startup smoke checks; this is not full
interactive Windows or power-loss validation. See [Testing](testing.md).

## Android interrupted-save review

SAF uses direct provider writes, which are **not atomic**. A `content://` URI is
never treated as a filesystem path. A verified, checksummed save-intent record
is written before modification, with a completion record after shared read-back
and backup maintenance. Records contain hashes and backup identifiers, not a
second journal or a persisted unsaved draft.

Unfinished/damaged records, missing records for existing journals and unaccounted
pending backups block normal loading/saving and require review. Existing journals
can require a one-time review when first opened with this protection. Valid
syntax alone does not establish that a partial write preserved every transaction.

**Settings → Review interrupted save** shows current-version classification,
validation and associated backup status. Cancel changes nothing. After pausing
external synchronization and inspecting the files, explicitly keep the present,
validated version or restore externally and review again. Keeping a reviewed
version creates a protected snapshot and archives the prior metadata; it does
not rewrite the journal or replay transactions. Missing/invalid journals must
be fixed externally before acceptance.

The native queue prevents operations within one Flutter process overtaking a
still-running save after timeout/cancellation. It is not a cross-process lock.
Provider caching, power loss, revoked grants and removal/rollback of recovery
evidence remain limitations. Read-back proves observed bytes, not durability.
See [Android recovery sequence and device checklist](android-storage-validation.md#current-android-save-recovery)
for exact ordering and physical-device verification limits. Original PoC phone
success does not certify later interrupted-save or multi-device behavior.
