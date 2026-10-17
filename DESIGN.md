---
name: "Проверка кассовых операций"
description: "Restrained visual system for local video review and original evidence."
colors:
  bg: "#f5f5f4"
  surface: "#fff"
  ink: "#252529"
  muted: "#65636a"
  line: "#e3e1e4"
  accent: "#7b143e"
  accent-hover: "#601031"
  tint: "#f8eef2"
  high: "#a22238"
  high-bg: "#fff0f2"
  medium: "#855400"
  medium-bg: "#fff5df"
  low: "#245c82"
  low-bg: "#edf5fb"
  unspecified: "#62566a"
  unspecified-bg: "#f0edf1"
  field-line: "#cfcbd0"
  secondary-hover: "#f4f1f3"
  selected-line: "#d3a6b9"
  video-ground: "#1d1e23"
  sampled: "#e9e7eb"
  sampled-line: "#beb9c3"
  incomplete: "#f0d7a4"
  unknown: "#d9d3df"
  unknown-line: "#9d94a6"
  event-marker: "#6f5c68"
  selected-interval: "#d6acc0"
  notice-bg: "#fff3f5"
  notice-line: "#e6bcc6"
  notice-text: "#8b1730"
typography:
  headline:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "30px"
    fontWeight: 650
    lineHeight: 1.22
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.35
  body:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.55
  caption:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  badge: "4px"
  control: "6px"
  drop-zone: "8px"
  media: "10px"
  surface: "12px"
spacing:
  compact: "8px"
  small: "12px"
  medium: "16px"
  control: "20px"
  space: "24px"
  panel: "28px"
  wide: "32px"
  gutter: "40px"
  page-bottom: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "11px 20px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 20px"
  button-secondary-hover:
    backgroundColor: "{colors.secondary-hover}"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 12px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.surface}"
    padding: "28px"
  badge-unspecified:
    backgroundColor: "{colors.unspecified-bg}"
    textColor: "{colors.unspecified}"
    rounded: "{rounded.badge}"
    padding: "4px 8px"
  badge-high:
    backgroundColor: "{colors.high-bg}"
    textColor: "{colors.high}"
    rounded: "{rounded.badge}"
    padding: "4px 8px"
  badge-medium:
    backgroundColor: "{colors.medium-bg}"
    textColor: "{colors.medium}"
    rounded: "{rounded.badge}"
    padding: "4px 8px"
  badge-low:
    backgroundColor: "{colors.low-bg}"
    textColor: "{colors.low}"
    rounded: "{rounded.badge}"
    padding: "4px 8px"
  event-selected:
    backgroundColor: "{colors.tint}"
    textColor: "{colors.ink}"
    padding: "20px 22px"
---

# Design System: Проверка кассовых операций

## Overview

**Creative North Star: "The Evidence Workbench"**

This is a restrained banking work surface: a light neutral ground, white bordered surfaces, graphite text and burgundy actions. The approved Forte-inspired direction supplies palette and material character; it does not imply an official bank identity. The Russian functional title identifies the task without introducing a product brand, logo or slogan.

Video, source timestamps and original evidence carry the visual weight. The interface supplies clear grouping and quiet controls so reviewers can inspect a signal and record what they observed. Research limitations are product facts, not aesthetic claims: event types, regulatory matches and guaranteed safety must never be suggested by visual polish or reassuring color.

**Key Characteristics:**
- Light neutral canvas and white, thinly bordered panels.
- Burgundy reserved for actions, focus and selection.
- Compact system typography with moderate corners.
- Source media foregrounded; no generated imagery or decorative charts.
- Explicit text labels for state, priority and uncertainty.

## Colors

The palette combines warm neutral working surfaces with a controlled burgundy accent. Frontmatter values are normative and extracted from `web/styles.css`; color names describe their implemented roles.

### Primary

- **Burgundy** (`accent`): primary actions, links, active workflow step, focus outlines, event timestamps and range input accent. **Deep burgundy** (`accent-hover`) marks actionable hover.
- **Pale rose** (`tint`): selected event rows, drag-over feedback and the research banner. **Rose selection** (`selected-interval`) marks the current timeline interval; `selected-line` reinforces the selected row.

### Neutral

- **Warm ground** (`bg`) separates the page from **white surfaces** (`surface`). **Graphite** (`ink`) carries headings and body copy; **muted graphite** (`muted`) carries secondary explanations.
- **Quiet divider** (`line`) defines panel boundaries. `field-line` gives form controls a firmer stroke; `secondary-hover` adds subtle hover feedback.
- **Media charcoal** (`video-ground`) frames source video without cropping it.
- **Sample grey** (`sampled`) and its dashed edge (`sampled-line`) identify sampled coverage. **Muted mauve** (`event-marker`) identifies signals. Amber `incomplete` and dashed mauve `unknown`/`unknown-line` distinguish incomplete and unknown coverage.

Priority is a separate semantic set: high uses `high`/`high-bg`, medium uses `medium`/`medium-bg`, low uses `low`/`low-bg`, and unspecified uses `unspecified`/`unspecified-bg`. The color is always accompanied by text. Error notices use the rose-red `notice-*` set and remain visibly distinct from selected controls.

**The Evidence Before Color Rule.** Color communicates interface state and review priority; it never certifies an event, legal conclusion or safe interval.

## Typography

**Body and heading font:** Segoe UI, with Arial and generic sans-serif fallbacks. There is no separate display or mono family. The local system stack supports a compact Russian working interface without external font requests.

### Hierarchy

- **Headline:** the frontmatter headline role is the upload page heading. Processing and result headings use a smaller (26px) variant; result headings become (23px) at the narrower breakpoints.
- **Title:** section headings use the title role. Detail headings use (19px); event-panel headings use (16px); supporting subheadings use (14px).
- **Body:** the root body role establishes the scale. Descriptive copy commonly reduces to (13px); explanatory text and form notes use (12px), with line heights up to (1.7) for longer facts. Longer explanations are constrained to (70ch).
- **Label:** form labels use the label role. Primary and secondary button labels use the same size with weight (650). Event labels use weight (600).
- **Caption:** timestamps, legends and evidence captions use compact secondary roles. Priority badges use (10px) and weight (600). Time displays use tabular numerals rather than a separate font.

**The Functional Heading Rule.** Headings name the current task or evidence; do not add campaign typography, slogans or a marketing hero.

## Layout

The centered page is capped at (1440px), with desktop side gutters (40px). The white header is (86px) high. A horizontal three-step workflow precedes the content and uses an underline for the current state. The spatial rhythm centers on the actual `--space` value (24px), with compact gaps for related labels and larger gaps between sections.

Upload places the main form beside a (300px) explanatory rail, separated by (44px). At viewport widths of at least (1500px), the rail becomes (330px), the gap becomes (60px), and the drop zone grows. Results place the player and attached timeline beside a (340px) event rail with a (24px) gap. Detail content places three evidence frames beside explanatory facts using a (1.5fr / 1fr) split and (32px) gap. Annotation fields form four columns, with narrow start/end fields.

At (1000px) and below, page gutters reduce to (24px), the upload rail becomes (250px), and the event rail becomes (280px). The access form stacks, primary actions fill available width, detail content becomes one column, annotation becomes two columns, and the version tag is hidden.

At (720px) and below, the header, upload layout, result heading and review workspace stack. Page gutters become (18px); panel padding tightens. The event list remains scrollable with a (320px) maximum height instead of the desktop (470px). Evidence stays in three columns with smaller gaps. Annotation actions and footer stack; annotation fields remain two columns. Source video uses `object-fit: contain` on every device. Fixed media heights reserve review space while preserving the entire image.

## Elevation & Depth

Surfaces are flat: the interface uses background contrast, thin borders and whitespace rather than ambient shadows. The selected event receives an inset (1px) rose stroke. A native modal dialog uses a translucent dark backdrop to isolate a full original frame. This is contextual depth for inspecting evidence, not decorative elevation.

**The Flat Surface Rule.** Use borders and tonal separation for working panels; reserve the existing inset selection stroke and dialog backdrop for state.

## Shapes

Moderate, consistent corners organize the interface. Main surfaces use `rounded.surface`; controls and evidence thumbnails use `rounded.control`; the upload drop zone uses `rounded.drop-zone`. Player and timeline share `rounded.media`, with opposing adjoining corners squared so they read as one unit. Badges use small corners rather than pills. The spinner and small status indicator are circular; panels and buttons are not.

Borders are generally (1px), with dashed boundaries for upload and coverage uncertainty. Focus uses a visible (2px) burgundy outline offset (4px), including the file input's wrapper. Inline line icons remain small and functional; original frames are never decorated with invented detections.

## Components

### Buttons

Primary buttons use burgundy fill, white text, control corners and the frontmatter padding, with a minimum height (46px). Secondary buttons use a white fill, graphite text and a field-colored border. Hover shifts the fill in (150ms) with ease; disabled buttons use opacity (0.45) and a disabled cursor. Text actions use burgundy without a surrounding box. Preserve visible keyboard focus on every variant.

### Inputs / Fields

Inputs, selects and textareas use white fill, a field-colored stroke, control corners, an explicit label and a minimum height (44px). The compact event sort select uses (36px). Input top spacing is (7px); placeholders remain muted and fully opaque. Focus follows the global outline. File selection uses a real transparent native input over its visible label; drag-over changes the drop zone to pale rose with a burgundy border. Checkbox and range accents are burgundy.

### Navigation

The workflow is an ordered step indicator, not a decorative tab bar. The active step uses `aria-current`, burgundy text, weight (650) and a bottom stroke (2px). The header title is a functional home link; the research version appears as a small bordered tag on wider screens. A skip link appears on keyboard focus.

### Cards / Containers

White panels use thin dividers and surface corners. Upload padding is (28px); evidence detail uses (26px 28px). Processing is centered in a panel capped at (680px), with real stage text, elapsed time and a spinner. It never introduces a fabricated completion percentage. Reduced-motion preferences disable both transitions and animation.

### Priority Badges and Notices

Priority badges use concise text, semantic tint pairs and small corners. Unspecified priority has its own neutral treatment and must remain available. The research banner is a compact rose band, while an error is a bordered notice with alert semantics. Empty event lists use plain explanatory text inside the rail.

### Event List

Full-width rows have a timestamp, event description and text priority badge. Rows separate with thin dividers. Hover adds a very light rose ground; selection uses pale rose plus an inset stroke and `aria-pressed`. Choosing an event coordinates the source player, selected interval and evidence detail. Scrolling is confined to the event list on longer results.

### Video, Timeline and Evidence

Native video controls sit above the attached white timeline. Layered coverage segments, event markers, a selected interval and a slim graphite playhead describe different facts; the legend and caution text remain visible. Timeline seeking also has a labeled native range control. Evidence thumbnails preserve the original frame with contain sizing and open a native dialog with an explicit close action. Captions retain timestamps and the before/event/after relationship. No source-media snippets are embedded in the design sidecar.

### Confirmation Form

Human annotation sits in a native disclosure below evidence. It collects time bounds, reviewer, action, observed fact and proposed rule, followed by an explicit confirmation checkbox. Save feedback is announced as status text. Keep model output and human confirmation distinguishable in copy and state.

## Do's and Don'ts

### Do:

- **Do** use the extracted system stack, neutral canvas, white bordered panels and burgundy action states.
- **Do** keep the original video and evidence visually prominent and preserve uncropped source media.
- **Do** pair every priority and coverage color with explanatory text, and preserve unspecified states.
- **Do** use visible keyboard focus, native controls and reduced-motion behavior.
- **Do** stack the workspace at the implemented narrow-screen breakpoint while retaining timestamps and evidence access.

### Don't:

- **Don't** invent a product name, logo, slogan, marketing hero or decorative chart.
- **Don't** generate or alter evidence imagery or add unsupported boxes, trajectories or detections.
- **Don't** use green reassurance, priority badges or polished presentation to imply guaranteed safety or a legal verdict.
- **Don't** display fabricated progress or uncalibrated confidence percentages.
- **Don't** imply confirmed event types, regulatory matches or generalization performance when the product evidence does not establish them.
