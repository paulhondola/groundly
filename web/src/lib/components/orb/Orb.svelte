<script lang="ts">
  import { theme } from "../../theme.svelte";
  import { cn } from "../ui/utils";
  import { type OrbMode, frame, geometry } from "./orb";

  interface Props {
    mode: OrbMode;
    size?: number;
    /** Set when the orb is the only thing saying what is happening; otherwise it is decoration. */
    label?: string;
    class?: string;
  }

  let { mode, size = 16, label = undefined, class: className = "" }: Props = $props();

  let canvas = $state<HTMLCanvasElement | null>(null);

  $effect(() => {
    const el = canvas;
    void theme.resolved; // the ink follows the theme
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
    const c = ctx;
    const geo = geometry(mode, size);
    const ink = getComputedStyle(el).color || "#e7eaee";
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    el.width = Math.round(size * dpr);
    el.height = Math.round(size * dpr);
    c.setTransform(dpr, 0, 0, dpr, 0, 0);

    function draw(t: number): void {
      const { dots, lines } = frame(geo, mode, size, t);
      c.clearRect(0, 0, size, size);
      c.fillStyle = ink;
      c.strokeStyle = ink;
      for (const l of lines) {
        c.globalAlpha = l.alpha;
        c.lineWidth = l.width;
        c.beginPath();
        c.moveTo(l.x1, l.y1);
        c.lineTo(l.x2, l.y2);
        c.stroke();
      }
      for (const d of dots) {
        c.globalAlpha = d.alpha;
        c.beginPath();
        c.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        c.fill();
      }
      c.globalAlpha = 1;
    }

    const still =
      mode === "idle" ||
      mode === "done" ||
      (typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (still) {
      draw(1.2);
      return;
    }

    let raf = 0;
    let visible = true;
    const loop = (now: number) => {
      draw(now / 1000);
      raf = visible && document.visibilityState !== "hidden" ? requestAnimationFrame(loop) : 0;
    };
    const resume = () => {
      if (raf === 0 && visible && document.visibilityState !== "hidden") {
        raf = requestAnimationFrame(loop);
      }
    };
    // Off screen or in a hidden tab the orb stops drawing.
    const observer =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver((entries) => {
            visible = entries.some((e) => e.isIntersecting);
            resume();
          });
    observer?.observe(el);
    document.addEventListener("visibilitychange", resume);
    draw(performance.now() / 1000);
    resume();

    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
      document.removeEventListener("visibilitychange", resume);
    };
  });
</script>

<!-- svelte-ignore a11y_no_interactive_element_to_noninteractive_role -->
<canvas
  bind:this={canvas}
  role={label ? "img" : undefined}
  aria-label={label}
  aria-hidden={label ? undefined : "true"}
  width={size}
  height={size}
  style={`width:${size}px;height:${size}px`}
  class={cn("shrink-0 select-none text-foreground", className)}
></canvas>
