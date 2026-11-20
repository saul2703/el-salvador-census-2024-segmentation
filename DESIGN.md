# Design System — CPV 2024 Demographic & K-Means++ Operate Workbench

## Aesthetic Direction & Usage Scene
- **Mode:** `Operate` (precision dark-room analytical ledger).
- **Palette:** Deep slate obsidian (`#0b1120` canvas, `#111827` primary work surface, `#172135` secondary control layer, `#29374d` 1px structural hairlines).
- **Tinted Foregrounds (No Pure Neutral Grays):** Primary text `#f4f7fb`, secondary text `#9db1cc` (blue-tinted slate, contrast ≥ 6.4:1 on `#111827`), muted labels `#7d93b2` (contrast ≥ 4.6:1).
- **Semantic Cluster Accents:**
  - Cluster 1 (Metropolitan Tech & High-Capital Hubs): `#38bdf8`
  - Cluster 2 (Semi-Urban Commuter & Industrial Corridors): `#34d399`
  - Cluster 3 (Remittance-Driven Transnational Towns): `#fbbf24`
  - Cluster 4 (High-Vulnerability Rural Agricultural Districts): `#f472b6`

## Typography & Hierarchy
- **Single Tuned Sans Family:** `"Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif` with `font-family: "Cascadia Mono", Consolas, monospace` reserved strictly for district/municipality codes, SQL DDL, and tabular figures.
- **Fixed `rem` Scale (No Fluid `clamp()`):** `0.75rem` (12px table badges), `0.8125rem` (13px dense cells), `0.9375rem` (15px body copy, 68ch max measure), `1.125rem` (18px panel title), `1.5rem` (24px workspace title).
- **Tabular Numerals:** `font-variant-numeric: tabular-nums` enforced across all ledgers, KPI strips, and comparison tables.

## Craft Floor & Modern Web Guidance Compliance
- **Zero Eyebrows / Kickers / Numbered Heading Prefixes.**
- **Zero Nested Cards:** Top KPI summary uses a continuous `1px` bordered horizontal ledger strip rather than disconnected hero cards.
- **Browser Surface Theming:** Custom `::selection`, `caret-color`, `:focus-visible` outline, `text-underline-offset: 0.22em`, and `scrollbar-color`.
- **Responsive Container-Query Tables & Layout Containment:** `.table-wrapper` uses `container-type: inline-size` with sticky headers and `@container (width < 600px)` stacked label injection; independent sections use `content-visibility: auto; contain-intrinsic-size: auto 300px auto 520px`.
