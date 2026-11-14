# Account and category icons

Use **Choose icon** while creating an account/category, including quick category
creation from a transaction, or in an existing entity's Edit dialog. Accounting
lists do not have separate icon-changing buttons.
Icons are optional. In a creation form, the picker saves only a draft and shows
a preview; the assignment is written only after creating the account/category.
Canceling the picker keeps the previous draft; canceling creation discards it.
Select a candidate, then Save; Remove icon also requires Save.
An Edit dialog shows the current icon and updates its preview when the picker
saves a replacement or removal; icon changes are saved independently of edits
to the entity's other fields. Both forms show a **Choose icon** heading above
the preview and right-aligned icon-only **Choose icon** and **Remove icon**
buttons; their tooltips identify the actions. Use **Remove icon** to open the
picker with removal selected, then Save to confirm or Cancel to keep the icon.

- **Choose icon** searches offline snapshots of all four collections formerly
  searched online: Material Icons, Material Symbols, Simple Icons and SVG Logos.
  The snapshots contain over 31,000 icons that pass the existing safety and
  proportion checks. Search recognizes icon names, collection aliases, categories,
  and common English, Ukrainian and Russian category terms; up to 100 matches are
  shown. Icons already assigned to accounts or categories are included too.
  Search stays on the device.
- **Import image** uses Flet's file picker with `with_data=True`, including on
  Android; imported bytes are not treated as filesystem paths. Users must have
  the rights to use their images. Importing/resizing does not change those rights.

Material Icons and Material Symbols are preferred for their consistent
monochrome style at small display sizes. Simple Icons and SVG Logos are also
available, preserving their original color treatment where applicable. Candidates
that fail the existing SVG-safety or proportion checks are excluded. Search also
includes previously assigned icons and their original attribution.

## Validation and display

Every bundled/imported candidate is validated before appearing. Inline images display
at **16 logical pixels** with proportions preserved, including picker previews.
Transaction rows use a **25.92-pixel** leading image in a 40-pixel-wide rounded,
theme-aware secondary-container background spanning the title and details.
Selected budget categories and default-account/commission settings use the same
16-pixel artwork centered in a 24-pixel slot as transaction-form dropdowns.
Parent-category selectors use that same selected-icon slot. Category menu labels
inherit native menu typography, matching account menu label size and weight.
Budget deletion confirmations retain the category's assigned icon.
Stored artwork and existing
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
open its Edit dialog rather than creating it again.
Large icon migrations may need several immutable records.

History is intentionally retained rather than deleted under a sync client.
Limits are 384 KiB per record, 4096 records and 16 MiB total per icon directory.
There is no automatic history compaction; archive/rebuild a full directory only
with all devices closed and after keeping an independent copy. Old records must
not be individually pruned because later revisions can refer to them.

## Licensing

Bundled Google Material Icons and Material Symbols are Apache-2.0. Simple Icons
and SVG Logos are CC0-1.0. Their source snapshots and full license texts are in
`src/app/icon_catalogue/`. Previously assigned Arcticons assets retain their
CC-BY-SA-4.0 attribution and license; the full text remains bundled.
The picker exposes source and license information for the selected asset.
Normalized artwork is marked as modified; CC-BY-SA assets retain their license.
Collection licensing does not grant trademark rights or override restrictions
that a brand places on its marks. Imported assets are marked user-provided with
unverified rights; the app does not invent an open license for them.

Sources: [Material Icons](https://github.com/material-icons/material-icons),
[Material Symbols](https://github.com/google/material-design-icons),
[Simple Icons](https://github.com/simple-icons/simple-icons),
[SVG Logos](https://github.com/gilbarbara/logos), and the
[Iconify collection snapshots](https://github.com/iconify/icon-sets/tree/master/json).

## Validation status

The implementation passed `uv run inv check`, Windows-target Pyright and
`uv run inv build-android`. APK inspection confirmed the catalogue, license texts
and native Pillow libraries are packaged. Synthetic browser checks covered
offline catalogue selection, attribution, image import, saving,
restart persistence, nested categories and daily reports at narrow widths.
The 16px display size was measured in browser-rendered Home balances/transaction
rows at narrow and desktop widths, and in picker candidates and previews.

Physical Android SAF icon operations, native image import/decoding and Windows
runtime behavior remain unverified. Before release, test each source on a phone,
restart offline, synchronize the complete sidecar directory to another device,
and verify that revoked folder access reports an error without changing the
journal. Build success and browser previews do not establish those behaviors.
