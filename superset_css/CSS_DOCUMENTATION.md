# Superset Dashboard CSS Documentation

**File:** `ael_superset.css`
**Dashboard:** Youth QApp Phoenix AEL

---

## Table of Contents

1. [Overview](#overview)
2. [Brand Colours](#brand-colours)
3. [Repository Layout](#repository-layout)
4. [Section-by-Section Breakdown](#section-by-section-breakdown)
   - [KPI Number Styling](#kpi-number-styling-big_number_total)
   - [KPI Glassmorphism Card Design](#kpi-glassmorphism-card-design-front-face)
   - [KPI Accent Colours Per Group](#kpi-accent-colours-per-group)
   - [KPI Flip Card Effect](#kpi-flip-card-effect)
   - [macOS Glassmorphism Back Face](#macos-glassmorphism-back-face)
   - [Section Headers](#section-headers)
   - [Small Sub-Header Pills](#small-sub-header-pills)
   - [Other Chart Cards](#other-chart-cards)
   - [Global Styles](#global-styles)
5. [Chart ID Reference Table](#chart-id-reference-table)
6. [How to Add a New KPI Chart to the Flip Effect](#how-to-add-a-new-kpi-chart-to-the-flip-effect)
7. [How to Apply in Superset](#how-to-apply-in-superset)
8. [Dark Mode Note](#dark-mode-note)
9. [Browser Compatibility](#browser-compatibility)

---

## Overview

`ael_superset.css` is a custom CSS stylesheet applied to the **Youth QApp Phoenix AEL** dashboard in Apache Superset. It provides:

- Brand-aligned visual styling using Quest Alliance colours
- Glassmorphism KPI cards with a hover flip effect that reveals metric definitions
- Colour-coded grouping of KPI cards (top row, lessons, assessments)
- Styled section headers, sub-header pills, and table headers
- Card treatments for all non-KPI charts (trend charts, distribution charts, tables)

**How to apply it in Superset:**

1. Open the target dashboard.
2. Click the three-dot menu (top right) and select **Edit dashboard**.
3. In the edit panel, open the **CSS** tab (or look for the CSS editor in the toolbar).
4. Paste the full contents of `ael_superset.css` into the editor.
5. Click **Save**.

The styles take effect immediately after saving. No page reload is required.

---

## Brand Colours

| Role | Hex | Usage |
|------|-----|-------|
| Brand blue | `#156fb5` | KPI numbers, borders, headers, links, table filter columns, info icons |
| Brand orange | `#f7941d` | KPI gradient, assessment card accents, table header cells, Top 10 Subjects border |

Both colours appear together as a linear gradient on KPI metric numbers:

```css
background: linear-gradient(135deg, #156fb5, #f7941d);
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
```

---

## Repository Layout

```
superset_css/
├── ael_superset.css        # The stylesheet applied to the dashboard
└── CSS_DOCUMENTATION.md    # This file
```

---

## Section-by-Section Breakdown

### KPI Number Styling (`big_number_total`)

Selector prefix: `[data-test-viz-type="big_number_total"]`

| Element | Rule | Effect |
|---------|------|--------|
| `.header-title` | `display: none` | Hides the chart title bar inside each KPI tile |
| `.header-controls` | `display: none` | Hides the three-dot control menu on each KPI tile |
| `.header-line` | `font-size: 2.5vw`, gradient text | Large, responsive metric number with blue-to-orange gradient fill |
| `.subheader-line` | `font-size: 14px`, brand blue | Metric label below the number |
| `.superset-legacy-chart-big-number`, `.chart-container` | `display: flex`, centered | Centres the number and label vertically and horizontally |

Three charts carry inline annotation text appended after their subheader label via `::after` pseudo-elements:

| Chart ID | Annotation |
|----------|-----------|
| 1368 | `(# of registered users)` |
| 1369 | `(# of learners who complete at least one lesson)` |
| 1370 | `(# of unique users who have logged at least once on App)` |

---

### KPI Glassmorphism Card Design (Front Face)

All KPI card containers receive a frosted-glass light appearance through:

```css
background: rgba(255, 255, 255, 0.78);
backdrop-filter: blur(16px) saturate(160%);
border-radius: 16px;
border: 1px solid rgba(255, 255, 255, 0.55);
box-shadow:
    0 2px 12px rgba(21, 111, 181, 0.10),
    inset 0 1px 0 rgba(255, 255, 255, 0.80);
```

On hover, the outer shadow deepens to give a lift effect:

```css
box-shadow: 0 6px 20px rgba(21, 111, 181, 0.22);
```

This base style is applied to all eleven KPI chart IDs (2518–2525, 2550, 2551) as well as the generic `[data-test-viz-type="big_number_total"] .dashboard-component-chart-holder`.

---

### KPI Accent Colours Per Group

Accent borders visually group related KPIs. The accents layer on top of the shared glassmorphism base.

**Top row — blue top border** (Accessed, Engaged, Unique Logins, Females/100 Males):

```css
.dashboard-chart-id-2522,
.dashboard-chart-id-2523,
.dashboard-chart-id-2524,
.dashboard-chart-id-2525 {
    border-top: 4px solid #156fb5 !important;
}
```

**Lessons group — blue left stripe** (Avg Lessons Completed, Avg Lessons Allocated, Lessons Completion %):

```css
.dashboard-chart-id-2518,
.dashboard-chart-id-2519,
.dashboard-chart-id-2550 {
    border-left: 4px solid #156fb5 !important;
    background: linear-gradient(135deg, #f0f7ff 0%, #ffffff 100%);
}
```

**Assessments group — orange left stripe** (Avg Assessments Completed, Avg Assessments Allocated, Assessment Completion %):

```css
.dashboard-chart-id-2520,
.dashboard-chart-id-2521,
.dashboard-chart-id-2551 {
    border-left: 4px solid #f7941d !important;
    background: linear-gradient(135deg, #fff8f0 0%, #ffffff 100%);
}
```

**LP% and AP% percentage tiles — full border highlight:**

The two completion-percentage cards (2550, 2551) receive a full perimeter border in addition to their left stripe, and a larger font size (`3vw`) to distinguish them as summary metrics within their group:

```css
.dashboard-chart-id-2550 {
    border: 2px solid #156fb5 !important;
    border-left: 4px solid #156fb5 !important;
}

.dashboard-chart-id-2551 {
    border: 2px solid #f7941d !important;
    border-left: 4px solid #f7941d !important;
}
```

---

### KPI Flip Card Effect

The flip effect reveals a definition panel when the user hovers over a KPI card. It is implemented entirely in CSS using five logical steps.

**Step 1 — Card container (`position: relative`)**

Each KPI card container is positioned relatively so that the `::before` and `::after` pseudo-elements can be absolutely positioned inside it:

```css
.dashboard-chart-id-2522, /* ... all 11 IDs ... */ {
    position: relative !important;
    cursor: pointer;
    overflow: visible !important;
}
```

**Step 2 — Front face: squish away on hover**

All direct children (`> *`) of the card container are the front-face content. On hover, they scale to zero width and fade out simultaneously:

```css
/* Resting state */
.dashboard-chart-id-2522 > * {
    transition: transform 0.25s ease, opacity 0.2s ease;
    transform-origin: center;
    transform: scaleX(1);
    opacity: 1;
}

/* Hover state — squish to zero */
.dashboard-chart-id-2522:hover > * {
    transform: scaleX(0);
    opacity: 0;
}
```

**Step 3 — Info icon (`::before`): fades out as the card flips**

A small circular "i" badge is placed in the top-right corner of each card using `::before`. It signals to the user that hovering reveals more information. It fades out as the back face appears:

```css
.dashboard-chart-id-2522::before {
    content: "i";
    position: absolute;
    top: 8px; right: 8px;
    width: 18px; height: 18px;
    background: #156fb5;
    color: white;
    border-radius: 50%;
    font-style: italic;
    opacity: 0.65;
    transition: opacity 0.2s ease;
    pointer-events: none;
}

.dashboard-chart-id-2522:hover::before {
    opacity: 0;
}
```

**Step 4 — Back face (`::after`): springs in with a slight overshoot**

The `::after` pseudo-element acts as the back face. It starts hidden (`scaleX(0)`, `opacity: 0`) and springs into view on hover. The transition uses a `cubic-bezier(0.34, 1.56, 0.64, 1)` spring curve, which causes the card to slightly overshoot its final scale before settling — giving a tactile, bouncy feel. There is a `0.20s` delay so it does not start until the front face has mostly finished squishing away:

```css
.dashboard-chart-id-2522::after {
    /* ... layout and glass styles ... */
    transform: scaleX(0);
    opacity: 0;
    transition: transform 0.28s cubic-bezier(0.34, 1.56, 0.64, 1) 0.20s,
                opacity 0.22s ease 0.20s;
}

.dashboard-chart-id-2522:hover::after {
    transform: scaleX(1);
    opacity: 1;
}
```

**Step 5 — Back face content per chart**

Each card has its own `content` string defining what text appears on the back face. The strings use CSS `\A` escapes to insert newlines (rendered with `white-space: pre-line`):

```css
.dashboard-chart-id-2522::after {
    content: "📋  ACCESSED\A" "─────────────\A" "Total unique users who\Asuccessfully registered on QuestApp";
    white-space: pre-line;
}
```

**CSS hex escape bug — safe use of `\A` in content strings**

In CSS, `\A` is a Unicode escape for U+000A (line feed / newline). However, a CSS escape sequence consumes up to **six hex digits** after the backslash. This means `\A` followed immediately by any hex character (`0–9`, `a–f`, `A–F`) forms a two-digit escape rather than the intended newline plus that character. For example:

- `"\Aat"` is parsed as `\Aa` (U+00AA, the feminine ordinal indicator `ª`) followed by `t` — not a newline followed by `at`.
- `"\A0"` is parsed as U+00A0 (non-breaking space) — not a newline followed by `0`.

The fix is to use CSS string concatenation by splitting the string at the point where `\A` precedes a hex character:

```css
/* Broken — \Aa is U+00AA, not newline + "at" */
content: "TITLE\Aat least one lesson";

/* Safe — separate string literals are concatenated by the CSS parser */
content: "TITLE\A" "at least one lesson";
```

Whenever the word immediately after a `\A` starts with a hex digit or hex letter, always split the string there.

---

### macOS Glassmorphism Back Face

The back face (`::after`) uses a dark navy frosted-glass aesthetic that evokes macOS system sheets.

**Base background — layered gradients:**

A specular highlight gradient (simulating light catching the top-left edge of the glass) sits on top of a semi-transparent dark navy base:

```css
background:
    linear-gradient(
        155deg,
        rgba(255, 255, 255, 0.10) 0%,
        rgba(255, 255, 255, 0.04) 30%,
        transparent 55%
    ),
    rgba(8, 22, 52, 0.82);
```

**Blur and saturation:**

```css
backdrop-filter: blur(28px) saturate(180%);
-webkit-backdrop-filter: blur(28px) saturate(180%);
```

This blurs and boosts saturation of whatever is behind the card, creating the frosted glass look. See [Browser Compatibility](#browser-compatibility) for fallback behaviour.

**Glass edge:**

```css
border: 1px solid rgba(255, 255, 255, 0.16);
```

A thin, semi-transparent white border simulates the bright edge of a glass pane.

**Shadow stack (inset top glow + deep outer shadow):**

```css
box-shadow:
    0 18px 50px rgba(0, 0, 0, 0.32),   /* deep outer drop shadow */
    0 4px 14px rgba(0, 0, 0, 0.18),    /* closer ambient shadow */
    inset 0 1px 0 rgba(255, 255, 255, 0.24),  /* inner top glow */
    inset 0 -1px 0 rgba(0, 0, 0, 0.12);       /* inner bottom shadow */
```

**Text:**

White at 95% opacity with a subtle text shadow for legibility against any background:

```css
color: rgba(255, 255, 255, 0.95);
text-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
```

---

### Section Headers

Full-width section headers (e.g. "Learning Activity", "Access and Engagement") receive a gradient blue background with white bold text.

The selector uses `:not(:has(.header-small))` to distinguish full section headers from the smaller sub-header pills, which are handled separately:

```css
.dashboard-component-header:not(:has(.header-small)) {
    background: linear-gradient(90deg, #156fb5, #2a9fd6) !important;
    border-radius: 10px !important;
    padding: 10px 18px !important;
}
```

All text elements inside the header (h3, span, `.header-title`, and their descendants) are forced to white and stripped of any inherited gradient text effect:

```css
.dashboard-component-header:not(:has(.header-small)) .header-title * {
    color: white !important;
    -webkit-text-fill-color: white !important;
    background: none !important;
    -webkit-background-clip: unset !important;
    font-weight: 700 !important;
}
```

The anchor link icon that Superset renders inside headers is hidden:

```css
.dashboard-component-header:not(:has(.header-small)) .anchor-link-container {
    display: none !important;
}
```

---

### Small Sub-Header Pills

Sub-headers (e.g. "Lessons", "Assessments") use a `.header-small` class and are styled as centered uppercase pills:

**Container** — transparent background, no border, flex-centered:

```css
.dashboard-component-header:has(.header-small) {
    background: transparent !important;
    border-bottom: none !important;
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
}
```

**Pill element:**

```css
.header-small {
    display: inline-block !important;
    background-color: #156fb5 !important;
    color: white !important;
    padding: 4px 18px !important;
    border-radius: 20px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 0.8px !important;
    text-transform: uppercase !important;
}
```

---

### Other Chart Cards

| Chart(s) | Style |
|----------|-------|
| **CC Slider (2531)** | Light blue-to-peach gradient, dashed brand blue border, subtle blue shadow |
| **Monthly trends (2529, 2530)** | Light grey border, soft neutral shadow, 16px rounded corners |
| **Location / Device / User Type (2526, 2527, 2528)** | Light grey border, minimal shadow, 16px rounded corners |
| **Details tables (2552, 2533, 2534)** | Blue-tinted grey border, blue-tinted shadow, 16px rounded corners |
| **Top 10 Subjects (2517)** | Orange top border (4px), orange-tinted shadow, 16px rounded corners |
| **Last Update (2547)** | Light grey background (`#f8f9fa`), grey border, small muted text |

---

### Global Styles

| Selector | Rule | Effect |
|----------|------|--------|
| `.css-1n97xmt` | `background-color: white !important` | Sets the overall dashboard canvas to white |
| `a` | `color: #156fb5 !important` | All hyperlinks use brand blue |
| `.dashboard-component.dashboard-component-header .anchor-link-container .fa.fa-link` | `display: none` | Hides anchor link icons on all dashboard headers |
| `.filter-counts.css-1kawqy8` | `display: none` | Hides the filter count badge |
| `.css-13rbkvc` | `background: #156fb5`, `border-radius: 30px` | Tab/section header pill in brand blue (legacy selector) |
| `th.dt-is-filter` | `background-color: #156fb5`, white text | Filter column headers use brand blue |
| `thead tr th` | `background-color: #f7941d`, white text | All other table header cells use brand orange |
| `div.cell-bar.positive` | `height: 15px`, `border-radius: 3px` | Bar chart cells in table rows get a rounded, compact bar |
| `.css-zxt2xr .css-c7w8t3` | `text-overflow: ellipsis` | Long chart labels are truncated with an ellipsis |
| `.css-1u8ar8f p` | `font-size: 11.5px`, brand blue, italic | CC Slider info text uses small brand-blue italic style |

---

## Chart ID Reference Table

| Chart ID | Metric Name | Group | Accent Colour |
|----------|-------------|-------|---------------|
| 2522 | Accessed | Top row | Blue top border (`#156fb5`) |
| 2523 | Engaged | Top row | Blue top border (`#156fb5`) |
| 2524 | Unique Logins | Top row | Blue top border (`#156fb5`) |
| 2525 | Females / 100 Males | Top row | Blue top border (`#156fb5`) |
| 2518 | Avg Lessons Completed | Lessons | Blue left stripe (`#156fb5`) |
| 2519 | Avg Lessons Allocated | Lessons | Blue left stripe (`#156fb5`) |
| 2550 | Lessons Completion % | Lessons | Blue full border (`#156fb5`) |
| 2520 | Avg Assessments Completed | Assessments | Orange left stripe (`#f7941d`) |
| 2521 | Avg Assessments Allocated | Assessments | Orange left stripe (`#f7941d`) |
| 2551 | Assessment Completion % | Assessments | Orange full border (`#f7941d`) |
| 2531 | CC Slider | Access & Engagement | Dashed blue border |
| 2526 | Location Distribution | Distribution | Light grey border |
| 2527 | Device Distribution | Distribution | Light grey border |
| 2528 | User Type Distribution | Distribution | Light grey border |
| 2529 | Monthly Trend (chart 1) | Trends | Light grey border |
| 2530 | Monthly Trend (chart 2) | Trends | Light grey border |
| 2552 | Completion Details (table 1) | Details | Blue-tinted border |
| 2533 | Completion Details (table 2) | Details | Blue-tinted border |
| 2534 | Completion Details (table 3) | Details | Blue-tinted border |
| 2517 | Top 10 Subjects | Subjects | Orange top border (`#f7941d`) |
| 2547 | Last Update | Footer | Light grey background |

---

## How to Add a New KPI Chart to the Flip Effect

To extend the flip effect to a new KPI chart, you need to touch **seven selector blocks** plus add one **Step 5 content rule**.

### Step 1: Find the chart ID in Superset

There are two ways to find the chart ID:

- **Chart context menu:** In the dashboard, hover over the chart, open its three-dot menu, and select **View chart source**. The URL will contain the chart ID (e.g. `/chart/2560`).
- **Dashboard YAML export:** Export the dashboard from **Manage → Dashboards → Export**. Open the YAML file and search for the chart's title — the `id` field next to it is the chart ID.

### Step 2: Add the ID to all seven selector blocks

Each block appears in the **KPI Card Flip Effect** section of the CSS. Open `ael_superset.css` and add `.dashboard-chart-id-NNNN` to each of the following groups:

| Block | What it controls |
|-------|-----------------|
| Step 1 — card container | `position: relative`, `overflow: visible` |
| Step 2 — front face resting | `transform: scaleX(1)`, `opacity: 1` |
| Step 2 — front face hover | `transform: scaleX(0)`, `opacity: 0` |
| Step 3 — info icon resting (`::before`) | blue "i" badge, `opacity: 0.65` |
| Step 3 — info icon hover (`::before`) | `opacity: 0` |
| Step 4 — back face resting (`::after`) | glass styles, `transform: scaleX(0)` |
| Step 4 — back face hover (`::after`) | `transform: scaleX(1)`, `opacity: 1` |

Example — adding chart ID 2560 to the Step 1 block:

```css
/* Before */
.dashboard-chart-id-2551 {
    position: relative !important;
    cursor: pointer;
    overflow: visible !important;
}

/* After */
.dashboard-chart-id-2551,
.dashboard-chart-id-2560 {
    position: relative !important;
    cursor: pointer;
    overflow: visible !important;
}
```

Repeat for all seven blocks.

### Step 3: Add the Step 5 content rule

Add a new `::after` rule with the tooltip text for the new chart. Place it alongside the other Step 5 rules:

```css
.dashboard-chart-id-2560::after {
    content: "ICON  METRIC TITLE\A" "─────────────\A" "Description line one.\A" "Description line two.";
    white-space: pre-line;
}
```

**CSS hex escape rule:** If the word that follows a `\A` newline escape starts with a hex digit or hex letter (`0–9`, `a–f`, `A–F`), split the string at that point using CSS string concatenation. The CSS parser treats `\A` as the start of a potentially multi-digit hex escape, so `"\Aat"` is read as `\Aa` (U+00AA) + `t`, not a newline + `at`. The fix:

```css
/* Broken */
content: "TITLE\Aat least once";

/* Safe — the parser sees \A as a complete escape, then starts a fresh string */
content: "TITLE\A" "at least once";
```

This applies to any hex character: `0`, `1`, `2`, `3`, `4`, `5`, `6`, `7`, `8`, `9`, `a`, `b`, `c`, `d`, `e`, `f` (case-insensitive).

---

## How to Apply in Superset

1. Open the **Youth QApp Phoenix AEL** dashboard.
2. Click the three-dot menu in the top-right corner of the dashboard.
3. Select **Edit dashboard**.
4. In the edit toolbar, click the **`</>`** (CSS) button or look for the **CSS** tab in the sidebar.
5. Select all existing content in the CSS editor and delete it.
6. Paste the full contents of `ael_superset.css`.
7. Click **Save**.

Changes apply to all users viewing the dashboard immediately after saving. You do not need to publish or reload the page.

---

## Dark Mode Note

Superset supports a dark mode that overrides many background colours. Whether a CSS rule in this file yields to dark mode or resists it depends on whether `!important` is used on the `background` property.

| Card group | `!important` on background? | Dark mode behaviour |
|------------|---------------------------|---------------------|
| Top row KPIs (2522–2525) | No (base `background` only) | Dark mode can override the background |
| Lessons KPIs (2518, 2519, 2550) | No | Dark mode can override the background — intentional, allows the blue-tinted `#f0f7ff` gradient to adapt |
| Assessments KPIs (2520, 2521, 2551) | No | Dark mode can override the background — intentional, allows the orange-tinted `#fff8f0` gradient to adapt |
| CC Slider (2531) | Yes (`!important`) | Resists dark mode; gradient is always shown |
| Dashboard canvas | Yes (`!important`) | Canvas is always white |
| Section headers | Yes (`!important`) | Always blue gradient, regardless of dark mode |

The lessons and assessments cards intentionally omit `!important` on their `background` so that Superset's dark mode theme can restyle them, while their accent borders (which do use `!important`) remain visible regardless of mode.

---

## Browser Compatibility

The `backdrop-filter` property used for glassmorphism effects (both the front-face base card and the back-face frosted glass panel) requires a modern browser.

| Browser | Minimum version | Notes |
|---------|----------------|-------|
| Chrome / Edge | 76+ | Full support |
| Safari | 9+ | Full support via `-webkit-backdrop-filter` prefix (included in the CSS) |
| Firefox | 103+ | Full support; earlier versions require enabling `layout.css.backdrop-filter.enabled` in `about:config` |

**Fallback behaviour:** Browsers that do not support `backdrop-filter` will still render the cards correctly. The card backgrounds fall back to their solid or semi-transparent `background` colour (e.g. `rgba(8, 22, 52, 0.82)` for the back face), so the text remains legible. The blur and saturation effect simply does not appear — the card is visible, just without frosted-glass aesthetics. No content is hidden or broken.
