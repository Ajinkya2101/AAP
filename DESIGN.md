---
name: ACSE AP
description: Plain, exact review screens for an Oracle EBS accounts-payable queue.
colors:
  ledger-blue: "#2856c9"
  ledger-blue-dark: "#7ea2ff"
  ink: "#1d1d1f"
  ink-dark: "#ececf0"
  pencil: "#6b6b70"
  pencil-dark: "#9a9aa3"
  rule-line: "#e3e3e6"
  rule-line-dark: "#34343a"
  paper: "#ffffff"
  paper-dark: "#17171a"
  blotter: "#f6f6f8"
  blotter-dark: "#202024"
  pass-green: "#1f7a3a"
  pass-green-dark: "#6fcf8a"
  warning-amber: "#9a6300"
  warning-amber-dark: "#e8b34a"
  fail-red: "#b3261e"
  fail-red-dark: "#ff8a80"
  bar-gray: "#8d8d96"
  bar-gray-dark: "#74747e"
typography:
  headline:
    fontFamily: "system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.45
  title:
    fontFamily: "system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.45
  body:
    fontFamily: "system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.45
  code:
    fontFamily: "ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
rounded:
  input: "4px"
  button: "5px"
  frame: "6px"
  panel: "8px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.ledger-blue}"
    textColor: "{colors.paper}"
    rounded: "{rounded.button}"
    padding: "4px 12px"
  button-default:
    backgroundColor: "{colors.blotter}"
    textColor: "{colors.ink}"
    rounded: "{rounded.button}"
    padding: "4px 12px"
  button-danger:
    backgroundColor: "{colors.blotter}"
    textColor: "{colors.fail-red}"
    rounded: "{rounded.button}"
    padding: "4px 12px"
  input-text:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "4px 6px"
  chip:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ledger-blue}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  chip-on:
    backgroundColor: "{colors.ledger-blue}"
    textColor: "{colors.paper}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  panel:
    backgroundColor: "{colors.blotter}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "10px 12px"
  tile:
    backgroundColor: "{colors.blotter}"
    textColor: "{colors.ink}"
    typography: "{typography.headline}"
    rounded: "{rounded.panel}"
    padding: "10px 12px"
  tile-link:
    backgroundColor: "{colors.blotter}"
    textColor: "{colors.ledger-blue}"
    typography: "{typography.headline}"
    rounded: "{rounded.panel}"
    padding: "10px 12px"
  input-search:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "4px 6px"
    width: "24em"
  table-cell:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "5px 8px"
---

# Design System: ACSE AP

## Overview

**Creative North Star: "The Ledger"**

The UI looks like an accounting ledger: plain, exact and tabular. Numbers line up in their columns, every row says where its value came from, and nothing on screen is there for decoration. Clerks spend hours at a desk working through a queue of invoices, so the screens are dense, quiet and predictable. The only things that should catch the eye are a flag, a failed check or the next action.

The system is small on purpose: one stylesheet, the operating system's own font, one accent color and three status colors. Depth comes from borders and a light gray panel, never from shadows. Dark mode follows the operating system and uses the same tokens.

There is one binding rejection, **no AI razzmatazz**: no sparkles, gradients, glowing "AI" badges, chat bubbles or animated thinking states. Where a model was involved, the screen shows it as a plain Method column (`extracted`, `value_map`, `fuzzy_llm`, `user`), the same way a ledger names its source.

**Key Characteristics:**
- Tables do most of the work, with hairline rules between rows and right-aligned tabular numbers.
- System font at 14px, with the hierarchy set by weight rather than size.
- Flat: borders and a gray panel tint, no shadows.
- One blue for links and the main action; green, amber and red only for pass, warning and fail.
- Light and dark modes switch automatically with the operating system's setting.

## Colors

A neutral paper-and-ink palette with one blue and three status colors. Every token has a dark-mode pair (the `-dark` keys), switched by `prefers-color-scheme`.

### Primary
- **Ledger Blue** (`ledger-blue`; `ledger-blue-dark` in dark mode): links, the selected filter chip, the one primary button ("Approve and post to Oracle"), the figure in a dashboard tile that links to its records, and ticked checkboxes. Nothing else uses it.


### Neutral
- **Ink** (`ink`): body text and values.
- **Pencil** (`pencil`): secondary text, meaning table headers, source values, methods, timestamps and "acting as".
- **Rule Line** (`rule-line`): every border and table rule.
- **Paper** (`paper`): the page background and input fields.
- **Blotter** (`blotter`): panels (review tasks, the decision form) and the default button.

### Status
- **Pass Green** (`pass-green`): a passed validation result.
- **Warning Amber** (`warning-amber`): a warning result and field flags.
- **Fail Red** (`fail-red`): a failed result, errors, Oracle rejections, the Failed figure when it is not zero, and the text of the Reject and "Delete selected" buttons.

### Data
- **Bar Gray** (`bar-gray`; `bar-gray-dark` in dark mode): the bars and columns in the dashboard's Breakdown. They are neutral on purpose, so that color keeps meaning status and nothing else. A hovered column turns Ink.

### Named Rules
**The Color Is Status Rule.** Green, amber and red mean pass, warning and fail, and nothing else. Blue means "link" or "the main action", and nothing else. A color that doesn't carry one of those meanings doesn't belong on the screen.

**The Two Palettes Rule.** Every new color is added as a light/dark pair in the `:root` block and its `prefers-color-scheme: dark` override. Never hard-code a hex value in a template. The one exception is the white background behind the original-file frame, which stays white because the document itself is white.

## Typography

**Body Font:** system-ui (with sans-serif)
**Label/Mono Font:** the browser's default monospace, used for IDs and keys

**Character:** the operating system's own face, so the UI looks native and works offline. Hierarchy comes from weight (600–700) and the muted color, while sizes stay within a 12–18px band.

### Hierarchy
- **Headline** (700, 18px, 1.45): the page title, e.g. "Dashboard" or "Supplier · Invoice #". The figure in a dashboard tile uses the same size.
- **Title** (700, 15px, 1.45): section headings on the review screen (Invoice, Validation, Agent decisions, Holds, Oracle, History) and the dashboard's zones (Upload invoices, Records, Breakdown). Sub-sections inside the Breakdown use the Label style (600, 14px).
- **Body** (400, 14px, 1.45): all table cells, text and form fields.
- **Label** (600, 14px): table headers (in Pencil, never wrapping), the labels of the upload and search forms, and the sub-section headings inside the dashboard's Breakdown.
- **Code** (monospace, 12px): document IDs, submission keys and agent-decision JSON.

### Named Rules
**The System Font Rule.** No web fonts. The stack is `system-ui, sans-serif`, and the UI has to render fully with no network.

**The Tabular Numbers Rule.** Every amount, quantity and price is right-aligned with `font-variant-numeric: tabular-nums` so the digits line up down the column.

## Layout

A full-width top bar (brand, Dashboard, Admin, API, and "Acting as" pushed to the right) above a main area with 16px side padding. The pages are full-width tables. On the review screen there is a two-column grid: the original file on the left (5 fr) and the facts and decision form on the right (6 fr), with a 16px gap. It collapses to one column below 1000px. The original-file frame is 80vh tall. The dashboard stacks, top to bottom: a strip of figure tiles (the ones with a queue behind them are links to it), the upload form, the records (search, filter chips, the table, paging at 50 rows), and last the Breakdown: a grid of small sections (the received-per-day columns, a bar list, two short tables) that wraps at 300px per column. The records come before the breakdown because the queue is the work.

The spacing is tight: table cells 5px × 8px, panels 10px × 12px, 8px between stacked panels, 16–20px above section headings. The screens are built for a desktop session on a wide monitor. On narrow screens the layout stacks but isn't optimized.

## Elevation & Depth

Completely flat. Areas are separated by 1px Rule Line borders and the Blotter tint on panels. There are no shadows.

### Named Rules
**The No Shadow Rule.** `box-shadow` is never used. If something has to stand apart, give it a border or put it in a Blotter panel.

## Shapes

Small, even corner radii that grow with the size of the element: inputs 4px, buttons 5px, the file frame and flash messages 6px, panels 8px. Filter chips are full pills (999px). Every edge is a 1px solid Rule Line border, and the decision form's buttons are the only filled shapes.

## Components

### Buttons
Compact and literal: the label says exactly what happens ("Approve and post to Oracle", "Reject").
- **Shape:** gently rounded (5px), 1px border, padding 4px 12px, inherits the body font.
- **Primary:** Ledger Blue fill with Paper text. One per form, for the action that moves the invoice forward.
- **Default:** Blotter fill with Ink text and a Rule Line border.
- **Danger:** the default button with Fail Red text. Destructive actions use colored text, not a red fill. A destructive action repeated on every table row (Delete) is a plain default button, so a column of them does not read as a column of failures; the one bulk button above the table ("Delete selected") is the Danger button.
- **Hover / Focus:** browser defaults, with no custom transitions.

### Chips
- **Style:** pill (999px), Rule Line border, Ledger Blue link text, padding 2px 10px. Each chip carries a filter and its count: `All 1067`, `Need Attention 1063` (every status that needs a person), then one per status, e.g. `IN_REVIEW 4`. A chip keeps the current search.
- **State:** the selected chip is filled Ledger Blue with Paper text and carries `aria-current`. Chips are filter links, never decoration.

### Cards / Containers
- **Corner Style:** 8px.
- **Background:** Blotter.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px Rule Line.
- **Internal Padding:** 10px 12px.
- Used for review tasks (reason codes in bold, suggested action muted, summary in pre-formatted text), and the decision form.

### Figure Tiles
- A Blotter panel (8px, Rule Line border, 10px 12px) holding one figure in the Headline size with tabular numbers over a Pencil label. The strip wraps at 150px per tile.
- A tile whose figure has records behind it is a link to those records, and its figure is Ledger Blue. A tile that links nowhere keeps an Ink figure.
- Figures are counts a person can act on. A tile is not a place for a chart, an icon or a trend arrow.

### Bars
- **Bar list:** label · bar · count on one line; the longest bar is the full width, bars are 10px high in Bar Gray with the data end rounded 4px, and the count is always printed. Status labels are filter links.
- **Columns:** received per day, 90px high on a Rule Line baseline, first and last date under it; the exact count is the column's tooltip.
- No legends, gradients or animation: one series per chart, named by its heading. A breakdown with a single category is not shown, and status counts live on the filter chips, not in a chart.

### Inputs / Fields
- **Style:** 1px Rule Line border, 4px radius, Paper background, padding 4px 6px, full width by default. The narrow sizes are 7em for a PO line and 13em for a GL account string.
- **Focus:** browser default focus ring.
- **Placeholder:** shows the real format, e.g. `01-510-6420-0000`.
- **Search:** the same field at 24em with a bold label in front and a default button after it; the placeholder names what it searches ("Invoice number, supplier or file name"). A "Clear" link appears only while a search is active.
- **Checkbox:** the browser's own, tinted Ledger Blue (`accent-color`). The header checkbox selects the page and shows the indeterminate state when some rows are ticked.

### Navigation
- **Style:** a plain row of links in the top bar, separated from the page by a 1px bottom rule. The brand "ACSE AP" is in bold. The current page's link is bold (600) and underlined, while the others are plain blue links with no underline. It wraps on narrow screens.

### Ledger Table (signature)
The main component. The table is full width with collapsed borders and a 1px Rule Line under every row. Headers are Pencil at 600 weight, cells are top-aligned, and number columns are right-aligned with tabular numbers. Review data is always laid out as a table with its provenance next to the value: Field · Value · On the invoice · Method · Flags. An empty state is a single muted row that says what is empty and links the way out ("No invoices in this filter. Show all").

The records table adds three things: a narrow checkbox column on the left, a plain Delete button at the end of each row, and a line above it with the one Danger button ("Delete selected", disabled until a row is ticked) and the count of ticked rows. It shows 50 rows; under it a muted "1–50 of 1067" sits with Previous and Next links.

## Do's and Don'ts

### Do:
- **Do** use a table for any list of records, with number columns right-aligned in tabular numbers.
- **Do** show the source value and method next to every extracted value.
- **Do** use Oracle EBS terms and codes as they are (`QTY REC`, `IN_REVIEW`, `01-510-6420-0000`).
- **Do** add every color as a light/dark pair in the `:root` tokens.
- **Do** keep a single primary (Ledger Blue) button per form.

### Don't:
- **Don't** add AI razzmatazz: no sparkle icons, gradients, glowing "AI" badges, chat bubbles, typing or thinking animations, or "magic" wording.
- **Don't** use `box-shadow`, anywhere.
- **Don't** load web fonts or any asset from a CDN. The UI has to work offline.
- **Don't** use green, amber, red or blue for anything other than status, links and the main action.
- **Don't** fill destructive buttons red. Use red text on the default button.
