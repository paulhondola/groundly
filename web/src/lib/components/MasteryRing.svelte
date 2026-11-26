<script lang="ts">
  interface Props {
    /** 0..1 */
    ratio: number;
    size?: number;
    label: string;
  }

  let { ratio, size = 40, label }: Props = $props();

  // r = 15 in a 36 box: circumference ≈ 94.2.
  const CIRCUMFERENCE = 2 * Math.PI * 15;
  const filled = $derived(Math.max(0, Math.min(1, ratio)) * CIRCUMFERENCE);
</script>

<!-- The only progress shape on the page. -->
<svg width={size} height={size} viewBox="0 0 36 36" role="img" aria-label={label} class="shrink-0">
  <circle cx="18" cy="18" r="15" fill="none" stroke="var(--border)" stroke-width="3.5" />
  {#if filled > 0}
    <circle
      cx="18"
      cy="18"
      r="15"
      fill="none"
      stroke="var(--status-mastered)"
      stroke-width="3.5"
      stroke-linecap="round"
      stroke-dasharray={`${filled} ${CIRCUMFERENCE}`}
      transform="rotate(-90 18 18)"
    />
  {/if}
</svg>
