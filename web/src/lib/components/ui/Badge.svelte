<script module lang="ts">
  import { tv, type VariantProps } from "tailwind-variants";

  export const badgeVariants = tv({
    base: "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        destructive: "border-transparent bg-destructive/15 text-destructive",
        mastered:
          "border-transparent bg-[color-mix(in_oklab,var(--status-mastered)_16%,transparent)] text-[var(--status-mastered)]",
        progress:
          "border-transparent bg-[color-mix(in_oklab,var(--status-progress)_16%,transparent)] text-[var(--status-progress)]",
        awaiting:
          "border-transparent bg-[color-mix(in_oklab,var(--status-awaiting)_16%,transparent)] text-[var(--status-awaiting)]",
      },
    },
    defaultVariants: {
      variant: "secondary",
    },
  });

  export type BadgeVariant = VariantProps<typeof badgeVariants>["variant"];
</script>

<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { cn } from "./utils";

  type Props = {
    variant?: BadgeVariant;
    class?: string;
    children?: Snippet;
  } & Omit<HTMLAttributes<HTMLSpanElement>, "class">;

  let { variant = "secondary", class: className = "", children, ...rest }: Props = $props();
</script>

<span class={cn(badgeVariants({ variant }), className)} {...rest}>
  {@render children?.()}
</span>
