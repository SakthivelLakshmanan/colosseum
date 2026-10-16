# Colosseum Design System (CDS)
**Specification & Architectural Standards for the Decision Command Room**
*Version 1.0 — Confidential / Internal*

---

## 1. Design Philosophy: The Decision Command Room

Colosseum is not a gaming platform, a generic SaaS dashboard, or a toy chatbot. It is an **executive decision intelligence harness** for high-stakes capital allocation, security, compliance, and enterprise risk.

The visual and interactive tone must evoke a **calm, futuristic, corporate "Decision Command Room"**:
- **Calm Authority**: Muted, deep navy-charcoal surfaces that recede into the background, allowing critical signals (objections, evidence strikes, scores) to command immediate attention.
- **Precision Over Flair**: Crisp 1px glass borders, tabular numbers, monospace evidence identifiers, and razor-sharp typographic hierarchy.
- **Focused Motion**: Motion is strictly functional—it directs eye movement, signals pipeline progress, and confirms actions without gratuitous flair.
- **Zero Neon Fatigue**: Accent colors (electric cyan and semantic alerts) are applied with deliberate restraint. Large swathes of high-saturation color or loud gradients are strictly forbidden.

---

## 2. Color System & Design Tokens

Colosseum uses standard CSS variables scoped to `:root` (dark mode by default) and overridden under `[data-theme="light"]`.

### 2.1 Theme Palettes

| Token | Dark (Default) | Light | Usage |
| :--- | :--- | :--- | :--- |
| `--bg-base` | `#0B1020` | `#F4F7FB` | Canvas / viewport background |
| `--bg-surface` | `rgba(18, 26, 46, 0.75)` | `rgba(255, 255, 255, 0.88)` | Primary frosted glass panels |
| `--bg-surface-elevated`| `rgba(26, 38, 64, 0.85)` | `#FFFFFF` | Popovers, modals, drawers, cards |
| `--bg-surface-hover` | `rgba(34, 48, 80, 0.90)` | `rgba(241, 245, 249, 0.95)`| Interactive element hover state |
| `--border-subtle` | `rgba(255, 255, 255, 0.08)`| `rgba(15, 23, 42, 0.10)` | Standard 1px panel boundaries |
| `--border-strong` | `rgba(255, 255, 255, 0.16)`| `rgba(15, 23, 42, 0.20)` | Active, focused, or separator borders |
| `--border-glow` | `rgba(62, 230, 255, 0.35)` | `rgba(2, 132, 199, 0.35)` | Primary interactive highlight |
| `--text-primary` | `#F0F4F8` | `#0F172A` | Titles, values, primary content (WCAG AAA) |
| `--text-secondary` | `#94A3B8` | `#475569` | Explanations, metadata, labels (WCAG AA) |
| `--text-muted` | `#64748B` | `#94A3B8` | Watermarks, disabled indicators, icons |

### 2.2 Semantic Meaning Tokens
Semantic colors are **strictly functional**. They must never be repurposed for generic accents or decorative illustrations.

| Semantic Role | Token | Dark Value | Light Value | Meaning in Colosseum |
| :--- | :--- | :--- | :--- | :--- |
| **Primary Accent** | `--accent-cyan` | `#3EE6FF` | `#0099B8` | Active seats, selected options, focus rings, primary actions |
| **PASS / Kept** | `--color-pass` | `#10B981` | `#059669` | Passed final check, approved stances, accepted claims |
| **FLAG / Incomplete** | `--color-flag` | `#F59E0B` | `#D97706` | Verification warning, conditional stance, pending check |
| **STRIKE / Blocking** | `--color-strike` | `#EF4444` | `#DC2626` | Uncited fact strike, adviser objection, blocking finding |
| **Human Decision** | `--color-human` | `#8B5CF6` | `#7C3AED` | Executive override, human approval, CRO/CEO intervention |

### 2.3 WCAG AA Contrast Compliance
- All normal text (`<18pt` or `<14pt bold`) guarantees at least **4.5:1** contrast ratio against panel backgrounds.
- All large text (`≥18pt` or `≥14pt bold`) guarantees at least **3.0:1** contrast ratio.
- Monospace chips on dark backgrounds use high-luminance text (`#E2E8F0` or specific semantic foreground tints).

---

## 3. Typography

All typography is loaded from Google Fonts:
1. **Space Grotesk** (`500`, `600`, `700`) — Headings, stage titles, executive verdicts. Futuristic yet structured.
2. **Inter** (`400`, `500`, `600`) — Body copy, claims, decision rationales, descriptions. Clean and highly legible at 1080p.
3. **JetBrains Mono** (`400`, `500`, `600`) — Evidence IDs (`FIN-01`, `SEC-04`), metrics, token counts, timestamps, tabular score values.

### Type Scale

| Element | Font Family | Size / Line Height | Weight | Tracking |
| :--- | :--- | :--- | :--- | :--- |
| **Display / H1** | Space Grotesk | `2rem (32px)` / `1.2` | 700 | `-0.02em` |
| **H2 / Section** | Space Grotesk | `1.5rem (24px)` / `1.3` | 600 | `-0.01em` |
| **H3 / Card Header** | Space Grotesk | `1.125rem (18px)` / `1.4`| 600 | `0` |
| **Body Large** | Inter | `1rem (16px)` / `1.5` | 400 / 500 | `0` |
| **Body Small** | Inter | `0.875rem (14px)` / `1.4`| 400 | `0` |
| **Code / Evidence** | JetBrains Mono | `0.8125rem (13px)` / `1.4`| 500 | `0.02em` |
| **Label / Eyebrow** | Space Grotesk | `0.6875rem (11px)` / `1.2`| 600 | `0.08em` (UPPERCASE) |

---

## 4. Surfaces & The Frosted Glass Effect

Panels use frosted glass rendering with subtle depth:
```css
.glass-panel {
  background: var(--bg-surface);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: 
    0 4px 24px -1px rgba(0, 0, 0, 0.25),
    inset 0 1px 0 0 rgba(255, 255, 255, 0.05);
}
```

### Background Grid & Radial Glow
The viewport background features a 40px subtle grid and a slowly drifting radial spotlight:
- **Grid**: `linear-gradient(to right, rgba(255,255,255,0.025) 1px, transparent 1px)`
- **Radial Ambient**: A 600px radial gradient anchored around the top-center (The Arena) that softly shifts across 24 seconds, simulating calm command-room ambient illumination.

---

## 5. Motion & Interaction Standards

All animation adheres strictly to the following parameters:
- **Default Easing**: `cubic-bezier(0.2, 0.8, 0.2, 1.0)` (Smooth deceleration without spring bounce)
- **Durations**:
  - Micro-interactions (hover, focus, toggles): `180ms – 240ms`
  - Structural transitions (drawers, panels, toasts): `320ms – 450ms`
  - Ambient cycles (radial drift, soft working pulse): `2400ms – 4000ms`

### Provided Motion Patterns
1. **Staggered Panel Entry (`.animate-rise`)**: Panels fade in and rise 12px with staggered delay based on CSS variable `--delay`.
2. **Number Counters (`animateCounter()`)**: Numbers smoothly count up from 0 to their target value on stage completion.
3. **Bar Fills (`.score-bar-fill`)**: Score and progress bars animate their width smoothly from `0%` to their target percentage.
4. **Soft Working Pulse (`.is-working`)**: Subtle opacity cycle (`0.65` to `1.0`) and scale (`1.0` to `1.03`), conveying active LLM reasoning without jarring blinks.
5. **Shimmer Skeleton (`.skeleton`)**: Dual-layer linear gradient sliding smoothly across text and block silhouettes during data fetching.
6. **Side Drawers (`.drawer`)**: Slide in from the right edge with a frosted backdrop overlay.
7. **Toasts (`.toast`)**: Ascend from bottom-right with auto-dismiss progress indicators.

### Accessibility (`prefers-reduced-motion`)
When the user requests reduced motion, all animations, transforms, and transitions collapse to instantaneous state changes (`0.001s`).

---

## 6. Signature Element: The Arena

The Arena is the central visual centerpiece of Colosseum. It is an SVG semicircle depicting the **6 Domain Advisers** sitting in council around the central **Decision Node**:

```
                         [ Business ]
             [ Finance ]              [ Customer ]
                                
      [ Ops ]                                    [ Legal ]
                     ( DECISION NODE )
              [ InfoSec ]          [ ... ]
```

### 6.1 The 6 Advisers
1. **Finance** — Cost, ROI, multi-year CAPEX/OPEX.
2. **Business** — Revenue growth, strategic alignment, speed-to-market.
3. **Customer** — UX impact, churn risk, Net Promoter Score.
4. **Legal** — Regulatory liability, contractual binding, IP exposure.
5. **InfoSec** *(Blocker)* — Data residency, encryption, threat vulnerability.
6. **Ops** — Maintainability, SLA compliance, migration overhead.

### 6.2 Seat States & Visual Feedback
- **`idle`**: Dim translucent disc, faint boundary (`rgba(255,255,255,0.12)`), line hidden.
- **`scored`**: Routing score badge rendered below adviser name (`0.00`–`1.00`), boundary glow intensity proportional to score.
- **`selected`**: Electric cyan ring (`#3EE6FF`), connecting SVG line active with electric light pulse toward center node.
- **`working`**: Breathing glow pulse (`#3EE6FF` / `1.8s` loop) denoting an active Gemini reasoning loop.
- **`done`**: Border ring dynamically colored by stance:
  - Green (`#10B981`) = Approve
  - Amber (`#F59E0B`) = Conditional
  - Red (`#EF4444`) = Object
- **`blocking`**: High-priority crimson ring (`#EF4444`), pulsing warning aura, and shield icon indicating a veto.

---

## 7. Component Library

### 7.1 Buttons
- **Primary Glow (`.btn-primary`)**: Cyan accent background with subtle box-shadow glow. Used for primary user flow (e.g. *Run Decision*, *Approve Memo*).
- **Secondary (`.btn-secondary`)**: Frosted glass background with light 1px border. For secondary controls (e.g. *Inspect Evidence*, *Filter*).
- **Ghost (`.btn-ghost`)**: Transparent background with hover fill. Used for inline utilities, dismissals, and tabs.

### 7.2 Badges & Chips
- **Badge (`.badge`)**: Compact status pill. Variants: `.badge-pass`, `.badge-flag`, `.badge-strike`, `.badge-human`, `.badge-cyan`.
- **Chip (`.chip`)**: Monospace interactive token for evidence citations (`[FIN-01]`, `[SEC-03]`) and tags. Supports click-to-inspect.

### 7.3 Progress Step Bar
Horizontal pipeline tracker displaying run phases:
`1. Routing` $\to$ `2. Evidence` $\to$ `3. Advisers` $\to$ `4. Debate` $\to$ `5. Scribe` $\to$ `6. Verdict`.
Active stages pulse with `--accent-cyan`; completed stages lock into green checkmarks.
### 7.4 Scorecard Row & Bar
Displays criterion evaluation across options:
- Criterion name and weight (`Weight: 1.0`).
- Dual score bars comparing Option A vs Option B (`0.0`–`5.0`).
- Evidence chips linked to supporting documentation.

### 7.5 Verdict Seal
An authoritative, stamp-like executive badge rendered in the header of finalized decision memos:
- **PASS**: Mint emerald border and badge, confirming no blocking objections and verification checks passed.
- **FLAG**: Amber warning seal denoting conditional requirements.
- **STRIKE / REJECT**: Crimson seal indicating an unresolved veto or evidence strike.
- **HUMAN OVERRIDE**: Violet seal denoting intervention by CRO, CEO, or Investment Committee.

---

## 8. Responsive Layout Architecture

- **Max Container Width**: `1280px` centered with auto margins.
- **Grid Layout**: 12-column responsive CSS grid with 16px to 24px gap.
- **Mobile Handheld (`375px` to `640px`)**:
  - 16px side gutters.
  - Semicircle Arena transforms gracefully into an adaptive touch-friendly carousel or compact viewBox without horizontal scrolling.
  - Drawers expand to full viewport width (`100vw`).
  - Scorecard rows stack criteria vertically.

---

## 9. Rule of Implementation for Future Web Steps
Every subsequent page and component implemented in `web/` must:
1. Reference CSS variables from `styles.css`. No hardcoded hex color values in inline styles.
2. Use designated font stacks (`Space Grotesk`, `Inter`, `JetBrains Mono`).
3. Follow the semantic meaning color mapping without exception.
4. Maintain dark mode by default with complete light mode compatibility.
5. Ensure zero horizontal scrollbars at `375px` screen width.
