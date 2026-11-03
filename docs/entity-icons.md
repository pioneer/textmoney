# Account and category icons

Use **Choose icon** while creating an account/category, including quick category
creation from a transaction, or beside an existing entity and in its Edit dialog.
Icons are optional. In a creation form, the picker saves only a draft and shows
a preview; the assignment is written only after creating the account/category.
Canceling the picker keeps the previous draft; canceling creation discards it.
Select a candidate, then Save; Remove icon also requires Save.

- **Choose icon** searches 30 bundled Google Material outlined icons offline.
  Search recognizes common English, Ukrainian and Russian category terms.
- **Search more online** sends only the explicitly entered query to Iconify.
  Nothing is searched automatically from account names, transactions or balances.
  No API key, account or developer-operated server is needed. Searches are cached
  for the app session and rate-limited; public-service availability is not
  guaranteed. There is no scraping, automatic retry or alternative-host bypass.
- **Import image** uses Flet's file picker with `with_data=True`, including on
  Android; imported bytes are not treated as filesystem paths. Users must have
  the rights to use their images. Importing/resizing does not change those rights.

Online search covers Material Icons, Material Symbols, Simple Icons
and SVG Logos. The restricted collection list permits shipping and retaining
their license texts and attribution, rather than assuming every Iconify
collection has the same terms. Matching outlined Material results come first,
then other Material, then Simple Icons, then colored SVG Logos.
This is deterministic collection/style ranking, not visual AI classification.
Arcticons is excluded entirely from online results: its thin strokes do not match
the app at small display sizes. Previously assigned Arcticons assets and their
attribution remain available offline; imports are unchanged. This also removes the Arcticons
Monobank result. Coverage of individual brands such as Monobank and Silpo is not
guaranteed; import remains the fallback. Other collections are not subject to
automatic per-image stroke-thickness analysis.

## Validation and display

Every downloaded/imported candidate is validated before appearing. Inline images display
at **16 logical pixels** with proportions preserved, including picker previews.
Transaction rows use a **28.8-pixel** leading image in a 40-pixel-wide slot spanning
the title and details. Stored artwork and existing
assignments are unchanged by display sizing:

- PNG/JPEG/WebP: non-animated, at most 2 MiB input, each side 48–4096 pixels,
  aspect ratio between 1:2 and 2:1. Re-encode as PNG, remove original metadata,
  downscale to a maximum 96-pixel square and add transparent padding. Smaller
  accepted images are not upscaled. The original import is not modified.
- SVG: scalable dimensions are accepted, but a finite positive `viewBox`, the same
  aspect-ratio bounds and a small inert paths/shapes subset are required.
  Scripts, references, embedded images, styles, filters, DTDs/entities and other
  unsupported constructs are rejected. Some otherwise legitimate complex SVGs
  therefore require conversion to PNG outside the app.
- Monochrome catalogue icons follow the current theme color. Imported images and
  colored logos retain their colors. The picker previews the actual 16px size.
  Transaction-form dropdowns center the same artwork in a fixed 24px icon slot.

Assigned icons appear in lists, balances, transaction movements/details,
account/category and parent selectors, commission/default-account settings,
daily/Overview reports and budget trees/filters/definitions. Labels, technical
identities and hierarchy indentation are retained.
Transaction movements with icons use consistent source/destination columns, including
opening balances and rows without a description. Inline amounts do not move the
arrow; the destination column has extra room for the amount on narrow screens.
Long account/category labels truncate instead of overflowing.
In both Operations and Home's recent activity, expense, income and reimbursement
category icons move to the leading slot. Transfers use the destination account's
icon there; opening balances use the opened account's icon. That icon is not
repeated beside its movement label. Other account icons stay inline. Rows without
an assigned leading icon keep their existing layout; no fallback artwork is added.
Theme colors, image proportions and attribution are retained.

## Storage and synchronization

The selected journal folder contains **`textmoney-icons/`**, accessed through
ordinary desktop files or the existing Android SAF grant. No icon metadata is
written into Beancount. Synchronize this entire directory with the journal.
Assignments include normalized image bytes, source, attribution, license ID/URL
and full catalogue license text, so both rendering and attribution work offline.

Sidecars are immutable content-addressed JSON revision records. They include
previous assignment heads; removals are explicit tombstones. Desktop publishes
a flushed staged record without replacing an existing record. SAF creates a new
document and verifies its name and bytes; **this is not an atomic SAF write**.
Neither icons nor failures modify the journal or its backups.

Conflicting concurrent assignments are reported, not silently chosen by clocks.
Reopen Choose icon on an affected entity and save to resolve its known heads.
Missing parent records report incomplete sync; damaged or renamed conflict
copies produce a visible error. An interrupted SAF record may require restoring
or removing that damaged sidecar from a verified independent copy; the app does
not automatically delete it. Previously loaded icons may remain visible with an
error notice until a successful reload.

Sidecars reload on attachment, explicit Reload and approximately every 30 seconds.
Saving rechecks current assignment heads. Local category subtree migrations move
assignments after the journal commit; display-name changes keep the same ID.
Entity deletion writes an icon tombstone after journal deletion. These auxiliary
operations are not part of the journal's atomic commit; post-commit failures
explicitly distinguish the already-saved journal from failed icon maintenance.
Creation also commits the journal before its optional icon sidecar. If icon
saving fails, the creation dialog closes and a visible error explains that the
entity already exists. Fix icon storage and use **Choose icon** on that entity;
do not create it again.
Large icon migrations may need several immutable records.

History is intentionally retained rather than deleted under a sync client.
Limits are 384 KiB per record, 4096 records and 16 MiB total per icon directory.
There is no automatic history compaction; archive/rebuild a full directory only
with all devices closed and after keeping an independent copy. Old records must
not be individually pruned because later revisions can refer to them.

## Licensing

Bundled Google Material artwork is Apache-2.0. Its source snapshot and full license
are in `src/app/icon_catalogue/`. Online Material collections use Apache-2.0,
and Simple Icons/SVG Logos CC0-1.0. Previously assigned Arcticons assets retain
their CC-BY-SA-4.0 attribution and license; the full text remains bundled.
The picker exposes source and license information for the selected asset.
Normalized artwork is marked as modified; CC-BY-SA assets retain their license.
Collection licensing does not grant trademark rights or override restrictions
that a brand places on its marks. Imported assets are marked user-provided with
unverified rights; the app does not invent an open license for them.

Sources: [Iconify public API](https://iconify.design/docs/api/),
[Material Icons](https://github.com/google/material-design-icons),
[Arcticons](https://github.com/Arcticons-Team/Arcticons),
[Simple Icons](https://github.com/simple-icons/simple-icons),
[SVG Logos](https://github.com/gilbarbara/logos).

## Validation status

The implementation passed `uv run inv check`, Windows-target Pyright and
`uv run inv build-android`. APK inspection confirmed the catalogue, license texts
and native Pillow libraries are packaged. Synthetic browser checks covered
bundled selection, online search, attribution, image import, saving,
restart persistence, nested categories and daily reports at narrow widths.
The 16px display size was measured in browser-rendered Home balances/transaction
rows at narrow and desktop widths, and in picker candidates and previews.

Physical Android SAF icon operations, native image import/decoding and Windows
runtime behavior remain unverified. Before release, test each source on a phone,
restart offline, synchronize the complete sidecar directory to another device,
and verify that revoked folder access reports an error without changing the
journal. Build success and browser previews do not establish those behaviors.
