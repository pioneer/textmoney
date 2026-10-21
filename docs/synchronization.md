# Local synchronization conflict safety

TxnCount remains offline-first. `txncount.beancount` is the only active financial
journal. Dropbox/Dropsync (or another external tool) moves files; TxnCount neither
contacts its API nor knows when synchronization is complete.

## Detection and preservation

On opening, polling and before saving, desktop and Android SAF adapters list
root sibling Beancount documents and protected `.txncount-sync/` evidence.
Candidates are recognized by journal/conflict filename terms, shared transaction
UUIDs, or exact hashes already present in revision records. Detection is not
timestamp-based and is not limited to Dropbox filenames. Unrelated file contents
are not logged or modified. Symlinks, ambiguous SAF names and malformed evidence
are refused rather than followed or guessed.

Exact snapshots use `<SHA-256>.beancount` names. Parent/child records describe
validated application-derived revisions; separate `head-<hash>.json` records
identify successfully verified saves. Candidates are preserved before writing,
so a derivation record alone does **not** prove that a save committed.
Surviving divergent committed heads expose replaced main journals, including
valid-looking shortened versions replaced while the app was closed.
An exact append that retains every original directive unchanged can reload
normally; this does not invent a merge base. Appending metadata to the previous
last directive is not an unchanged extension.
In-session removal/modification also preserves the previous loaded version.

Both conflicting byte sequences are read-verified and retained before offering
resolution. Hash names deduplicate repeated preservation. Desktop evidence
uses exclusive random staging, file fsync and replacement, then supported
directory synchronization; interrupted staging is not mistaken for a completed
snapshot. Android creates/read-verifies immutable provider documents. A partial
SAF snapshot or an incomplete synchronized evidence set blocks saving.
No claim of SAF atomicity or physical durability is made.

Ordinary `backups/` retention never touches `.txncount-sync/`. Evidence is retained
even after resolution; there is currently **no automatic evidence pruning**.
This deliberately favors recovery over bounded storage. It contains full private
financial snapshots: protect and synchronize it like the journal, including
hidden files. Each scan currently reads the evidence set; accumulated history
can cost storage, bandwidth and polling time.

## Merge eligibility

A common ancestor must be a hash-verified snapshot connected to both versions
by explicit recorded parent/child edges. A unique latest shared ancestor is
required. Arbitrary backups, similar text, file dates and synchronizer timestamps
are never used to guess ancestry. Missing/damaged referenced records block writing;
absent ancestry otherwise selects manual review.

Transaction merging requires valid UUID metadata, a complete lossless parser
round-trip and an identical nontransaction skeleton in both versions and the
base. Blank separator differences are ignored for eligibility, not rewritten
through a simplified serializer. Exact transaction spans are spliced into the
local source; remote-only chosen spans are appended. Full candidate validation
still applies, including references, currencies and explicit posting balance.

| Change | Behavior |
| --- | --- |
| Identical UUID and exact content | Keep once |
| Distinct new UUIDs from a recorded base | Propose both |
| One side unchanged from base, other edited/deleted | Propose the changed side |
| Independent edits to distinct UUIDs | Propose both |
| Same UUID with different independent changes | Explicit local/remote choice |
| Deletion versus modification | Explicit choice, including absence |
| No trustworthy base | Every differing transaction needs a choice |
| Account/category/currency/options/directive differences | Whole-version review or external manual merge |
| Transaction comments, missing/invalid IDs, unsupported syntax | No granular merge; whole valid version or external manual merge |

Even independent declaration additions are deliberately **not auto-merged**.
Proving declaration semantics and comment attachment is beyond this bounded
transaction merger. Transfers, exact total prices, fees, reimbursements,
adjustments and custom metadata are retained as source, not regenerated.
Unknown constructs never silently disappear in a transaction merge. A user may
explicitly choose a whole valid version, with a warning that it can omit changes.

## Review and guarded commit

1. Pause synchronization on all devices and take an independent folder copy.
2. Open **Settings → Review synchronization conflicts**. Choose a version.
3. Review added/modified/deleted UUIDs, dates, descriptions, counterparties,
   explicit postings and prices. Read-only source details expose metadata.
   Proven choices are preselected; ambiguous choices start empty. There is no
   unattended merging and ordinary transaction conflicts need no text editor.
4. For declaration/unsupported differences, expand the two read-only complete
   versions and explicitly choose a valid whole version, or compare and merge
   preserved files externally. Invalid candidates cannot be committed.
5. Cancel safely to postpone. Neither source nor any original conflict file is deleted.
6. Confirmation checks the main hash and exact captured source inventory,
   preserves originals and candidate, validates the complete result, and uses
   the existing `StorageSession.write`. The final desktop/native SAF guard
   checks sibling **and auxiliary evidence** bytes again before journal mutation.
7. Existing verified backups, desktop atomic replacement, Android prepared
   recovery intent, read-back and retention remain in force. After verified
   completion, record a head and content-addressed acknowledgment, then reload
   balances. Additional unresolved copies still block editing.

Acknowledgments cover only the reviewed filename/content hash and resolved
lineage. A changed conflict file reappears. An unrelated main-file rollback
cannot silently reuse an old acknowledgment. A crash or acknowledgment failure
may leave a saved result but require another review; the app does not replay the
financial operation. Android interrupted-save review takes precedence.

For damaged/missing evidence: stop writes, pause sync, copy the entire folder,
inspect the error and preserved versions, and restore matching evidence from a
trusted independent copy. Do not delete recovery metadata to force a save.
If repairing metadata is impossible, reconcile all versions externally into a
new dedicated folder, retain the original folder independently, and select the
new folder explicitly. Android's first-open recovery review may also be needed.

## Guarantees and limits

- **Interrupted local writes:** desktop replacement avoids truncating the main
  file before commit; Android direct writes require persistent interrupted-save
  review. These are distinct from synchronization conflict detection.
- **Concurrent local writers:** no new locks or compare-and-swap. Inventory and
  hash checks narrow, but cannot eliminate, the final TOCTOU race.
- **Asynchronous device divergence:** surviving copies/revisions provide evidence,
  not global consensus. Upload/download ordering may temporarily expose incomplete
  metadata; fail closed, wait for the external sync and review again.
- An arbitrary renamed file with no shared IDs, known hash or recognizable name
  cannot reliably be recognized as this journal. Unseen changes with no surviving
  copy/revision, or loss of all auxiliary evidence, cannot be reconstructed.
- Content hashes check integrity, not authorship against malicious folder writers.
  This is not a signed history, full Beancount engine or general version-control system.
- Evidence has no retention bound. Filesystem/provider permissions, power loss,
  externally deleted evidence and cloud conflict-file naming remain limitations.
  No global synchronization-complete or zero-data-loss guarantee is claimed.

## Verification

Synthetic tests in [test_synchronization.py](../tests/test_synchronization.py)
exercise divergent additions/edits/deletions, exact-once results, absent ancestry,
declaration/comment fallbacks, transfers with fees and official Beancount
validation, CRLF, detection/preservation, stale source/inventory guards,
post-commit acknowledgment failure, multiple copies, evidence corruption,
offline replacement and localized confirmation/cancellation.
Existing CRUD, backup, desktop atomic-write and Android recovery suites remain
regression coverage; source-decorating fixtures now explicitly review their
intentional external changes before editing.

Android coverage uses the existing fault-injected SAF model, including prepared
recovery, partial writes, timeouts/cancellation and failure after the candidate
is written. APK compilation checks the real Dart bridge; neither mocks nor
compilation prove SAF provider behavior. This change has no new physical-device,
real Dropbox/Dropsync, Windows desktop or power-loss validation.
Before using real data, test a disposable synced folder on two devices: add
independent transactions, edit the same UUID, delete versus edit, postpone,
change a source during review, interrupt a SAF save, and verify all preserved
copies and final balances after explicit recovery. Keep independent backups.
