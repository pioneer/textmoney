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

## Budget ordering

Budgets has a separate **Budget order** dialog beside its filter control when
the journal defines at least two budget categories. It lists all budget
categories, including those outside the selected month or filters. Drag a handle
or use the up/down arrows to move siblings; moving a parent moves its whole
subtree. Cancel discards the draft, Save explicitly publishes it, and Restore
default order takes effect only after Save. Default order follows category codes.
Names and assigned icons are displayed without financial amounts.

The order belongs to category codes, not dated limit records or currencies, and
applies across months. Display-name edits preserve positions. New categories
follow explicitly ordered siblings; missing categories are ignored. Budget
history remains chronological within each category.

Synchronize the complete `textmoney-budget-order/` directory alongside the
journal. It uses the same immutable revision, size-limit, stale-save and verified
publication rules as shared defaults, with a separate revision group and folder.
This isolation lets older app versions ignore ordering without breaking their
account or commission settings. Android uses the existing SAF grant and verifies
new documents; publication is not atomic.

Changes reload on journal attachment, opening the dialog, budget refresh and
normal shared-settings polling. An open draft never changes underneath the user.
A stale save is refused: close and reopen the dialog to review the latest order.
Conflicting histories show a warning and all candidate orders in the dialog;
arrange and Save to resolve the reviewed heads. Read failures retain the previous
order (or the default on initial load) with a visible warning, not a silent reset.
Uncertain writes require reopening and reloading before retrying.
Ordering does not modify the journal, calculations, selected month or filters.

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
