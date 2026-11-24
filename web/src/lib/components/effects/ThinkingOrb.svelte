<script lang="ts">
  import { MODE_FRAMES, paintFrame, resolvePreset } from "thinking-orbs/engine";
  import type { RunKind, RunPhase } from "../../types";
  import { theme } from "../../theme.svelte";
  import { cn } from "../ui/utils";
  import { type OrbSize, orbStateFor, phaseLabelFor } from "./orb-state";

  interface Props {
    phase: RunPhase;
    kind: RunKind;
    size?: OrbSize;
    label?: string;
    class?: string;
  }

  let { phase, kind, size = 32, label = undefined, class: className = "" }: Props = $props();

  let canvas = $state<HTMLCanvasElement | null>(null);
  const ariaLabel = $derived(label ?? phaseLabelFor(phase, kind));

  $effect(() => {
    const el = canvas;
    if (!el) {
      return;
    }
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = el.getContext("2d");
    } catch {
      ctx = null;
    }
    if (!ctx) {
      return;
    }
    const orbState = orbStateFor(phase, kind);
    const preset = resolvePreset(orbState, size);
    const frameFn = MODE_FRAMES[preset.mode];
    const dark = theme.resolved === "dark";

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    el.width = Math.round(size * dpr);
    el.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    function drawAt(t: number): void {
      ctx!.clearRect(0, 0, size, size);
      const frame = frameFn(size, t, preset.opts);
      paintFrame(ctx!, frame, dark);
    }

    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reducedMotion) {
      drawAt(0.6);
      return;
    }

    let rafId = 0;
    let inViewport = true;
    let t = 0.6;
    let lastTs: number | null = null;

    function isActive(): boolean {
      return inViewport && document.visibilityState !== "hidden";
    }

    function step(ts: number): void {
      rafId = 0;
      if (!isActive()) {
        lastTs = null;
        return;
      }
      if (lastTs !== null) {
        const dt = Math.min((ts - lastTs) / 1000, 0.1);
        t += dt * preset.speed;
      }
      lastTs = ts;
      drawAt(t);
      rafId = window.requestAnimationFrame(step);
    }

    function schedule(): void {
      if (rafId === 0 && isActive()) {
        rafId = window.requestAnimationFrame(step);
      }
    }

    function onVisibilityChange(): void {
      if (isActive()) {
        lastTs = null;
        schedule();
      } else if (rafId !== 0) {
        window.cancelAnimationFrame(rafId);
        rafId = 0;
      }
    }

    let observer: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver((entries) => {
        const visible = entries.some((entry) => entry.isIntersecting);
        inViewport = visible;
        if (visible) {
          lastTs = null;
          schedule();
        } else if (rafId !== 0) {
          window.cancelAnimationFrame(rafId);
          rafId = 0;
        }
      });
      observer.observe(el);
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    drawAt(t);
    schedule();

    return () => {
      if (rafId !== 0) {
        window.cancelAnimationFrame(rafId);
      }
      document.removeEventListener("visibilitychange", onVisibilityChange);
      observer?.disconnect();
    };
  });
</script>

<!-- svelte-ignore a11y_no_interactive_element_to_noninteractive_role -->
<canvas
  bind:this={canvas}
  role="img"
  aria-label={ariaLabel}
  width={size}
  height={size}
  style={`width:${size}px;height:${size}px`}
  class={cn("shrink-0 select-none", className)}
></canvas>
