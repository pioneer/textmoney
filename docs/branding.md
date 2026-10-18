# TxnCount branding

The canonical [assets/icon.png](../assets/icon.png) is original minimal artwork:
a green folder and white document on a dark blue background, 1024 x 1024 PNG.
It replaces the template's crossed square and is intentionally temporary.
Regenerate it with `uv run inv poc-icon`; validate with `uv run inv icon-check`.
There are no maintained duplicate platform icons. Invoke stages the root assets
for Flet, which generates launcher/window icons and splash screens.

Product name: **TxnCount**. Package/bundle: `com.pioneer.financepoc`, retained
from the first phone build so updates preserve data and persisted folder grants.
The generated/installed Android icon and display name still need phone inspection;
the canonical PNG has been visually inspected. No public release branding,
store signing, or production identity work is implied.
