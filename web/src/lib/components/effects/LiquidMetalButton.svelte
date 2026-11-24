<script lang="ts">
  import {
    LiquidMetalShapes,
    ShaderFitOptions,
    ShaderMount,
    getShaderColorFromString,
    liquidMetalFragmentShader,
  } from "@paper-design/shaders";
  import { theme } from "../../theme.svelte";
  import { cn } from "../ui/utils";

  interface Props {
    label: string;
    disabled?: boolean;
    onclick: (event: MouseEvent) => void;
    class?: string;
  }

  let { label, disabled = false, onclick, class: className = "" }: Props = $props();

  let hostEl = $state<HTMLDivElement | null>(null);
  let shaderReady = $state(false);

  $effect(() => {
    const el = hostEl;
    shaderReady = false;
    if (!el || disabled) {
      return;
    }

    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) {
      return;
    }

    let hasWebGL = false;
    try {
      const probe = document.createElement("canvas");
      hasWebGL = Boolean(probe.getContext("webgl2") || probe.getContext("webgl"));
    } catch {
      hasWebGL = false;
    }
    if (!hasWebGL) {
      return;
    }

    const dark = theme.resolved === "dark";
    let shaderMount: ShaderMount | null = null;
    try {
      shaderMount = new ShaderMount(
        el,
        liquidMetalFragmentShader,
        {
          u_colorBack: getShaderColorFromString(dark ? "#0f172a" : "#e2e8f0"),
          u_colorTint: getShaderColorFromString(dark ? "#38bdf8" : "#0284c7"),
          u_image: undefined,
          u_repetition: 2.5,
          u_softness: 0.45,
          u_shiftRed: 0.2,
          u_shiftBlue: 0.25,
          u_distortion: 0.15,
          u_contour: 0.4,
          u_angle: 110,
          u_shape: LiquidMetalShapes.none,
          u_isImage: false,
          u_fit: ShaderFitOptions.cover,
          u_scale: 1,
          u_rotation: 0,
          u_originX: 0.5,
          u_originY: 0.5,
          u_offsetX: 0,
          u_offsetY: 0,
          u_worldWidth: 0,
          u_worldHeight: 0,
        },
        undefined,
        0.35,
      );
      shaderReady = true;
    } catch {
      shaderReady = false;
      shaderMount?.dispose();
      shaderMount = null;
    }

    return () => {
      shaderReady = false;
      shaderMount?.dispose();
    };
  });
</script>

<button
  type="button"
  {disabled}
  {onclick}
  class={cn(
    "run-action relative inline-flex h-9 items-center justify-center overflow-hidden rounded-lg border border-border px-4 text-sm font-medium shadow-xs transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50",
    shaderReady
      ? "text-white"
      : "bg-gradient-to-r from-primary to-primary/85 text-primary-foreground hover:opacity-95",
    className,
  )}
>
  <div
    bind:this={hostEl}
    aria-hidden="true"
    class="pointer-events-none absolute inset-0 overflow-hidden rounded-[inherit]"
  ></div>
  <span
    class={cn(
      "relative z-10",
      shaderReady ? "rounded px-2 py-0.5 bg-slate-950/55 backdrop-blur-[2px]" : "",
    )}
  >
    {label}
  </span>
</button>
