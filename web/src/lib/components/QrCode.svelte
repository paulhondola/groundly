<script lang="ts">
  import { cn } from "./ui/utils";

  interface Props {
    matrix: boolean[][];
    quietZone?: number;
    label?: string;
    class?: string;
  }

  let {
    matrix,
    quietZone = 2,
    label = "Tailscale pairing QR code",
    class: className = "",
  }: Props = $props();

  const modules = $derived(matrix.length);
  const total = $derived(modules > 0 ? modules + quietZone * 2 : 0);
  const viewBox = $derived(total > 0 ? `0 0 ${total} ${total}` : "0 0 1 1");

  const pathData = $derived.by(() => {
    const parts: string[] = [];
    for (let y = 0; y < matrix.length; y += 1) {
      const row = matrix[y];
      if (!row) {
        continue;
      }
      for (let x = 0; x < row.length; x += 1) {
        if (row[x]) {
          parts.push(`M${x + quietZone},${y + quietZone}h1v1h-1z`);
        }
      }
    }
    return parts.join("");
  });
</script>

<svg
  {viewBox}
  role="img"
  aria-label={label}
  shape-rendering="crispEdges"
  class={cn("block h-44 w-44 rounded-lg border border-border bg-white text-black", className)}
>
  <path d={pathData} fill="currentColor" />
</svg>
