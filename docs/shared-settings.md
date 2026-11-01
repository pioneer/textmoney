# Shared journal defaults

Default transaction account and commission settings now belong to the selected
journal folder, not to a device. Commission branches and their default are one
setting; the account is independent. Language, theme, clock timezone, folder
grant, backup cleanup and other preferences remain local.

## First use and synchronization

In Settings, choose the account (or **No default account**) and Save. Choose
commission branches and their default and Save separately. Each Save explicitly
publishes that group. Nothing is published at startup or when selecting a folder.
Valid old device choices appear only as unsaved drafts when the group has no
shared history. Invalid old defaults produce an explanation without blocking the
journal. Old device preferences are not rewritten or used as a hidden fallback.
Until publication, new transactions have no default account and the usual
`Expenses:Bank-Fees` commission default.

Synchronize the **whole `textmoney-settings/` directory** with the journal.
Dropbox/Dropsync or another external client transports these local files;
TextMoney has no cloud API and cannot certify synchronization completion.
Finish editing and let synchronization complete before opening another device.
Shared defaults reload on attachment, explicit Reload, and roughly every
30 seconds while normal journal polling runs.

Loaded defaults apply only to newly opened transaction forms. Existing forms,
including commission choices when switching transaction types, keep their own
drafts. Editing saved transactions continues to use their own postings.
Clean Settings fields follow received values; unsaved Settings fields are retained.
Switching journal folders loads that folder's settings instead of carrying over
another folder's shared defaults.

Account/category codes are references, not display names. Missing/inactive
references remain visible in Settings with an explanation and are not replaced
with an arbitrary account/category. New forms leave that default unselected.
A later journal reload can make the reference available again. This covers
journal and settings arriving in either order.

## Conflicts and failures

Each group has immutable revision ancestry, not timestamp-based last-writer-wins.
Sequential changes supersede their observed ancestors. Changes to different
groups coexist. Equivalent parallel heads agree; differing heads produce a
visible conflict and no automatic default for that group. Settings shows all
candidate values. Choose the desired account, or commission branches/default,
then Save to resolve the known heads. No financial journal merge occurs.

Saving rereads the group's heads. If a draft is stale, Save refuses it and retains
input. **Review shared defaults** shows the latest choices. Continuing explicitly
acknowledges those versions while retaining the Settings draft for a later Save;
it does not publish or modify an open transaction. Cancel changes nothing.
Changes arriving during review invalidate that acknowledgement.

Malformed/renamed conflict copies, missing ancestors, oversized histories and
I/O failures produce visible errors, not silent resets. Settings saves remain
blocked until a successful Reload. Previously loaded defaults may remain in use
with a warning; on an initial failed load, neither default is preselected.
Financial journal access is independent. An uncertain settings write may have
created a record: do not blindly retry; reload and review first.

Category-code/parent migration updates shared commission references after the
verified journal commit. Display-name edits require no remapping. Journal and
settings are not one atomic transaction: an auxiliary failure explicitly reports
the completed migration and asks for Settings review/save, not another migration.
Unavailable references after deletion/reactivation remain explicit.

## Format and limits

Files are SHA-256-addressed UTF-8 JSON revisions:

```json
{
  "version": 1,
  "changes": {
    "account": {"parents": [], "value": "Assets:Cash"}
  }
}
```

An account clear writes `null`. A commission value is
`{"branches": ["Expenses"], "default": "Expenses:Bank-Fees"}`.
Parents are the prior filenames for that same group. Codes are private journal
references: sidecars are not encrypted, and values are never logged.
No settings metadata or additional directives are added to Beancount.

Desktop uses the same staged, flushed, no-replacement publication as icon
records. Android uses the existing SAF grant, creates a new document, and
verifies its name and bytes. **SAF publication is not atomic.**
An interrupted or damaged record requires inspection/restoration from a verified
complete copy; the app does not automatically delete it.

Limits: 64 KiB per record, 4096 records, 4 MiB total. There is no automatic
history pruning. Keep an independent copy; archive/rebuild the complete history
only with all devices closed. Never delete individual ancestors. Older app
versions ignore this sidecar and continue using device-local defaults; upgrade
all devices before relying on shared defaults.

## Device checks

Publish both groups on desktop, sync the whole folder, and open it on Android
with the existing SAF grant while offline. Verify new-form preselection, explicit
account clearing, commission branches, restart, reverse-direction changes and
draft preservation. Also test settings arriving before journal category changes,
folder switching, permission loss and incomplete/interrupted record transfer.
Host tests and APK compilation do not certify a physical SAF provider or sync client.
