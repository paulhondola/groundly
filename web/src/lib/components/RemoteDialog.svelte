<script lang="ts">
  import { createMutation, createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { apiClient, unwrap } from "../api/client";
  import { queryKeys } from "../api/queries";
  import QrCode from "./QrCode.svelte";
  import Badge from "./ui/Badge.svelte";
  import Button from "./ui/Button.svelte";

  interface Props {
    pairedCount?: number;
  }

  let { pairedCount = 0 }: Props = $props();

  const qc = useQueryClient();

  let open = $state(false);
  let copied = $state(false);
  let containerEl = $state<HTMLDivElement | null>(null);
  let copyTimer: ReturnType<typeof setTimeout> | null = null;

  const remoteQuery = createQuery(() => ({
    queryKey: queryKeys.remote(),
    queryFn: async ({ signal }) => unwrap(await apiClient.GET("/api/remote", { signal })),
    enabled: open,
    staleTime: 0,
    refetchInterval: open ? 3000 : false,
  }));

  const revokeMutation = createMutation(() => ({
    mutationFn: async (deviceId: number) =>
      unwrap(
        await apiClient.POST("/api/remote/devices/{id}/revoke", {
          params: { path: { id: deviceId } },
          body: {},
        }),
      ),
    onMutate: () => {
      revokeAllMutation.reset();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.remote() });
    },
  }));

  const revokeAllMutation = createMutation(() => ({
    mutationFn: async () => unwrap(await apiClient.POST("/api/remote/revoke-all", { body: {} })),
    onMutate: () => {
      revokeMutation.reset();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.remote() });
    },
  }));

  const devices = $derived(remoteQuery.data?.devices ?? []);
  const displayedCount = $derived(remoteQuery.data ? devices.length : pairedCount);
  const mutating = $derived(revokeMutation.isPending || revokeAllMutation.isPending);
  const queryError = $derived(
    remoteQuery.isError && !remoteQuery.data
      ? remoteQuery.error instanceof Error
        ? remoteQuery.error.message
        : "Could not load remote devices"
      : null,
  );
  const mutationError = $derived.by(() => {
    const err = revokeMutation.error ?? revokeAllMutation.error;
    if (!err) {
      return null;
    }
    return err instanceof Error ? err.message : "Could not update device access";
  });

  $effect(() => {
    if (!open || typeof document === "undefined") {
      return;
    }
    function onPointerDown(event: PointerEvent): void {
      const target = event.target;
      if (target instanceof Node && containerEl && !containerEl.contains(target)) {
        open = false;
      }
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        open = false;
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  });

  $effect(() => {
    return () => {
      if (copyTimer !== null) {
        clearTimeout(copyTimer);
      }
    };
  });

  async function copyPairingUrl(url: string): Promise<void> {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      copied = true;
      if (copyTimer !== null) {
        clearTimeout(copyTimer);
      }
      copyTimer = setTimeout(() => {
        copied = false;
        copyTimer = null;
      }, 2000);
    } catch {
      copied = false;
    }
  }

  function formatLastSeen(epochSeconds: number): string {
    try {
      return new Date(epochSeconds * 1000).toLocaleString([], {
        dateStyle: "short",
        timeStyle: "short",
      });
    } catch {
      return String(epochSeconds);
    }
  }
</script>

<div class="relative" bind:this={containerEl}>
  <Button
    variant="outline"
    size="sm"
    aria-expanded={open}
    aria-haspopup="dialog"
    onclick={() => {
      open = !open;
    }}
  >
    <span>Devices</span>
    {#if displayedCount > 0}
      <Badge variant="secondary" class="px-1.5 py-0 text-[11px]">{displayedCount}</Badge>
    {/if}
  </Button>

  {#if open}
    <div
      role="dialog"
      aria-label="Remote devices"
      class="absolute top-full right-0 z-40 mt-2 w-[min(22rem,calc(100vw-2rem))] space-y-4 rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-lg"
    >
      <div class="flex items-center justify-between gap-2">
        <h2 class="text-sm font-semibold text-foreground">Remote devices</h2>
        <Button variant="ghost" size="sm" onclick={() => (open = false)}>Close</Button>
      </div>

      {#if remoteQuery.isPending}
        <p class="text-xs text-muted-foreground">Loading devices…</p>
      {:else if queryError}
        <div
          class="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
          role="alert"
        >
          {queryError}
        </div>
      {:else if remoteQuery.data}
        {#if remoteQuery.data.active}
          <div class="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
            {#if remoteQuery.data.qr_matrix}
              <QrCode matrix={remoteQuery.data.qr_matrix} class="mx-auto" />
            {/if}
            <p class="text-xs leading-relaxed text-muted-foreground">
              Scan with your phone or tablet on Tailscale to pair this device.
            </p>
            {#if remoteQuery.data.pairing_url}
              {@const url = remoteQuery.data.pairing_url}
              <div class="flex items-center gap-2">
                <code
                  class="min-w-0 flex-1 truncate rounded border border-border bg-background px-2 py-1 font-mono text-[11px] text-foreground"
                >
                  {url}
                </code>
                <Button variant="secondary" size="sm" onclick={() => void copyPairingUrl(url)}>
                  {copied ? "Copied" : "Copy link"}
                </Button>
              </div>
            {/if}
          </div>
        {:else}
          <p class="rounded-lg border border-border bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
            Tailscale remote access is inactive in this session. Start Groundly with
            <code class="font-mono text-foreground">--tailscale</code> to show a pairing QR code.
          </p>
        {/if}

        <div class="space-y-2">
          <div class="flex items-center justify-between gap-2">
            <h3 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              Paired devices
            </h3>
            {#if devices.length > 0}
              <Button
                variant="outline"
                size="sm"
                disabled={mutating}
                onclick={() => revokeAllMutation.mutate()}
              >
                Revoke all
              </Button>
            {/if}
          </div>

          {#if mutationError}
            <div
              class="rounded-lg border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive"
              role="alert"
            >
              {mutationError}
            </div>
          {/if}

          {#if devices.length === 0}
            <p class="text-xs text-muted-foreground">No devices paired.</p>
          {:else}
            <ul class="space-y-2" role="list">
              {#each devices as device (device.id)}
                <li
                  class="flex items-center justify-between gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs"
                >
                  <div class="min-w-0 space-y-0.5">
                    <div class="truncate font-medium text-foreground">{device.label}</div>
                    <div class="truncate font-mono text-[11px] text-muted-foreground">
                      {device.peer_ip} · {formatLastSeen(device.last_seen_at)}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={mutating}
                    onclick={() => revokeMutation.mutate(device.id)}
                  >
                    Revoke
                  </Button>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</div>
