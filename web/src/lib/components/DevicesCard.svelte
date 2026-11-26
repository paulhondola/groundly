<script lang="ts">
  import { createMutation, createQuery, useQueryClient } from "@tanstack/svelte-query";
  import Smartphone from "@lucide/svelte/icons/smartphone";
  import { apiClient, unwrap } from "../api/client";
  import { queryKeys } from "../api/queries";
  import CopyField from "./CopyField.svelte";
  import QrCode from "./QrCode.svelte";
  import Button from "./ui/Button.svelte";

  const qc = useQueryClient();

  // Polled so a phone that just scanned shows up, and the single-use QR rotates in view.
  const remoteQuery = createQuery(() => ({
    queryKey: queryKeys.remote(),
    queryFn: async ({ signal }) => unwrap(await apiClient.GET("/api/remote", { signal })),
    staleTime: 0,
    refetchInterval: 3000,
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

  function formatLastSeen(epochSeconds: number): string {
    try {
      return new Date(epochSeconds * 1000).toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short",
      });
    } catch {
      return String(epochSeconds);
    }
  }
</script>

<section
  aria-labelledby="devices-heading"
  class="flex flex-col gap-5 rounded-2xl border border-border bg-card p-6"
>
  <h2
    id="devices-heading"
    class="text-[13px] font-medium tracking-[0.08em] text-subtle-foreground uppercase"
  >
    Study on another device
  </h2>

  {#if remoteQuery.isPending}
    <p class="text-sm text-muted-foreground">Loading devices…</p>
  {:else if queryError}
    <p class="text-sm text-destructive" role="alert">{queryError}</p>
  {:else if remoteQuery.data}
    {#if remoteQuery.data.active}
      <div class="flex flex-wrap items-center gap-4">
        {#if remoteQuery.data.qr_matrix}
          <QrCode matrix={remoteQuery.data.qr_matrix} class="h-36 w-36 shrink-0 rounded-xl border-0" />
        {/if}
        <p class="min-w-[12rem] flex-1 text-sm leading-relaxed text-foreground/85">
          Scan with your phone or tablet on Tailscale to pair it. Tests you take there are saved on
          this computer.
        </p>
      </div>
      {#if remoteQuery.data.pairing_url}
        <CopyField text={remoteQuery.data.pairing_url} label="Copy link" />
      {/if}
    {:else}
      <p class="text-sm leading-relaxed text-muted-foreground">
        Tailscale remote access is inactive in this session. Start Groundly with
        <code class="font-mono text-foreground">--tailscale</code> to show a pairing QR code.
      </p>
    {/if}

    <div class="flex flex-col gap-2">
      <div class="flex items-center justify-between gap-2">
        <h3 class="text-[13px] text-subtle-foreground">Paired · {devices.length}</h3>
        {#if devices.length > 0}
          <Button
            variant="ghost"
            size="sm"
            class="text-muted-foreground"
            disabled={mutating}
            onclick={() => revokeAllMutation.mutate()}
          >
            Revoke all
          </Button>
        {/if}
      </div>

      {#if mutationError}
        <p class="text-xs text-destructive" role="alert">{mutationError}</p>
      {/if}

      {#if devices.length === 0}
        <p class="text-xs text-muted-foreground">No devices paired.</p>
      {:else}
        <ul class="flex flex-col gap-2" role="list">
          {#each devices as device (device.id)}
            <li class="flex items-center gap-3 rounded-[10px] border border-border bg-background p-3">
              <Smartphone size={20} strokeWidth={1.4} class="shrink-0 text-muted-foreground" aria-hidden="true" />
              <div class="flex min-w-0 flex-1 flex-col gap-0.5">
                <span class="truncate text-sm text-foreground">{device.label}</span>
                <span class="truncate font-mono text-[11px] text-subtle-foreground">
                  {device.peer_ip} · {formatLastSeen(device.last_seen_at)}
                </span>
              </div>
              <Button
                variant="outline"
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
</section>
