<script lang="ts">
  import { authStore } from "../auth.svelte";
  import Card from "../components/ui/Card.svelte";

  $effect(() => {
    document.title = "Pair device · Groundly";
  });
</script>

<Card class="mx-auto max-w-md space-y-4 p-6">
  <div class="space-y-1">
    <h1 class="text-lg font-semibold text-foreground">
      {authStore.pairing ? "Pairing this device…" : "Pair this device"}
    </h1>
    <p class="text-sm text-muted-foreground">
      {authStore.pairing
        ? "Checking your single-use pairing link with Groundly."
        : "This Tailscale browser session is not paired with Groundly yet."}
    </p>
  </div>

  {#if authStore.error}
    <div
      class="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
      role="alert"
    >
      {authStore.error}
    </div>
  {/if}

  <p class="text-sm leading-relaxed text-muted-foreground">
    Open Groundly on your computer, then scan the current QR code shown in the terminal or under
    <span class="font-medium text-foreground">Devices</span> to pair this browser.
  </p>
</Card>
