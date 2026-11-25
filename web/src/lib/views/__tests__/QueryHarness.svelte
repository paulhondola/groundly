<script lang="ts">
  import { type QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import type { Component, Snippet } from "svelte";

  interface Props {
    client: QueryClient;
    children?: Snippet;
    component?: Component<any>;
    props?: Record<string, unknown>;
  }

  let { client, children, component: Comp, props: initialProps }: Props = $props();
  // svelte-ignore state_referenced_locally
  let viewProps = $state<Record<string, unknown>>(initialProps ?? {});

  export function setProps(next: Record<string, unknown>): void {
    viewProps = next;
  }
</script>

<QueryClientProvider {client}>
  {#if children}
    {@render children()}
  {:else if Comp}
    <Comp {...viewProps} />
  {/if}
</QueryClientProvider>
