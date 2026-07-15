---
name: Academic Precision Dark
colors:
  surface: '#111318'
  surface-dim: '#111318'
  surface-bright: '#37393e'
  surface-container-lowest: '#0c0e12'
  surface-container-low: '#1a1c20'
  surface-container: '#1e2024'
  surface-container-high: '#282a2e'
  surface-container-highest: '#333539'
  on-surface: '#e2e2e8'
  on-surface-variant: '#c8c4d3'
  inverse-surface: '#e2e2e8'
  inverse-on-surface: '#2f3035'
  outline: '#928f9d'
  outline-variant: '#474551'
  surface-tint: '#c5c0ff'
  primary: '#c5c0ff'
  on-primary: '#2a2179'
  primary-container: '#2f277e'
  on-primary-container: '#9993f0'
  inverse-primary: '#5953aa'
  secondary: '#c3c0ff'
  on-secondary: '#1d00a5'
  secondary-container: '#3626ce'
  on-secondary-container: '#b3b1ff'
  tertiary: '#89ceff'
  on-tertiary: '#00344d'
  tertiary-container: '#003954'
  on-tertiary-container: '#17a8ec'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e3dfff'
  primary-fixed-dim: '#c5c0ff'
  on-primary-fixed: '#130166'
  on-primary-fixed-variant: '#413a91'
  secondary-fixed: '#e2dfff'
  secondary-fixed-dim: '#c3c0ff'
  on-secondary-fixed: '#0f0069'
  on-secondary-fixed-variant: '#3323cc'
  tertiary-fixed: '#c9e6ff'
  tertiary-fixed-dim: '#89ceff'
  on-tertiary-fixed: '#001e2f'
  on-tertiary-fixed-variant: '#004c6e'
  background: '#111318'
  on-background: '#e2e2e8'
  surface-variant: '#333539'
typography:
  display-lg:
    fontFamily: Source Serif 4
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Source Serif 4
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Source Serif 4
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Source Serif 4
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.05em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  xs: 8px
  sm: 16px
  md: 24px
  lg: 40px
  xl: 64px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 48px
---

## Brand & Style
The design system focuses on intellectual rigor, clarity, and professional authority. It is tailored for high-density information environments such as research portals, academic journals, and institutional dashboards. 

The design style is **Corporate / Modern** with a lean towards **Minimalism**. It prioritizes legibility and structural hierarchy over decorative elements. By utilizing a dark color mode, the design system reduces eye strain for long-form reading and deep data analysis while maintaining a sophisticated, "midnight-campus" aesthetic. The interface should feel expansive, quiet, and deliberate.

## Colors
The palette is rooted in a deep navy-charcoal base (#0A0C10) to provide a stable, low-light environment. The primary brand blue (#2F277E) is reserved for high-significance actions and brand presence.

- **Primary:** Used for main buttons, active states, and selected navigation items.
- **Secondary:** A lighter, more vibrant violet-blue used for interactive accents and subtle highlights.
- **Neutral/Background:** The core canvas. Surface layers use slightly lighter variants of charcoal to define depth.
- **Semantic Colors:** Success (Emerald), Error (Rose), and Warning (Amber) should be desaturated to prevent jarring contrast against the dark background.

## Typography
The typography strategy pairings reflect a blend of traditional academia and modern technology. 

- **Headlines:** Use **Source Serif 4** to evoke the feeling of printed journals and authoritative texts. High-contrast serifs provide the necessary gravitas.
- **Body:** **Inter** is used for maximum readability in dense text blocks. Its neutral, systematic nature ensures that the interface stays out of the way of the content.
- **Labels/Data:** **JetBrains Mono** is employed for captions, metadata, and technical labels to provide a precise, organized feel.

## Layout & Spacing
This design system utilizes a **Fixed Grid** for desktop to maintain structural integrity and a **Fluid Grid** for mobile devices.

- **Grid:** A 12-column grid is used for desktop (max-width 1280px) with 24px gutters.
- **Rhythm:** Spacing follows a 4px/8px baseline shift. Layout margins should be generous (48px+) on desktop to create an editorial, airy feel.
- **Responsiveness:** On mobile, margins reduce to 16px. Columns collapse to a single-column stack, prioritizing vertical flow for reading.

## Elevation & Depth
In this dark mode iteration, depth is conveyed through **Tonal Layers** and **Low-contrast Outlines**. 

- **Surface 0:** The base background (#0A0C10).
- **Surface 1:** Used for sidebars and secondary containers.
- **Surface 2:** Used for cards and modals.
- **Outlines:** Borders are critical for defining boundaries in dark mode. Use a subtle 1px border (#30363D) instead of heavy shadows. 
- **Shadows:** When necessary (e.g., floating modals), use a deep, sharp shadow with 0% spread and a slight blue tint to distinguish the element from the void.

## Shapes
The shape language is **Soft**. A 4px (0.25rem) radius is applied to buttons and inputs to provide a modern touch without sacrificing the serious, professional tone. Larger containers like cards use an 8px (0.5rem) radius. Sharp corners are avoided to keep the interface approachable, but "pill-shaped" or highly rounded elements are excluded to maintain the formal aesthetic.

## Components
- **Buttons:** Primary buttons use the brand blue (#2F277E) with white text. Secondary buttons use a transparent background with a subtle border. 
- **Inputs:** Fields are dark-filled with a light border. On focus, the border transitions to the primary blue with a subtle outer glow.
- **Cards:** Cards should have no background fill on the primary surface, defined only by a 1px border, unless they are "Surface 2" elements meant to pop.
- **Lists:** Data lists use horizontal dividers with a 0.5pt weight. Selected items are indicated by a vertical primary-color bar on the left edge.
- **Chips:** Small, monochromatic chips for tagging and categorization, using the Monospaced label font.
- **Navigation:** A persistent sidebar or top-bar with low-opacity icons that brighten on hover.