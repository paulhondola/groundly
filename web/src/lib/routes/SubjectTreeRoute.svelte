<script lang="ts">
  import { decodeParam, route } from "../router";
  import NotFoundView from "../views/NotFoundView.svelte";
  import TreeView from "../views/TreeView.svelte";

  const subject = $derived(decodeParam(route.params.subject));
</script>

{#if subject !== null}
  <TreeView {subject} />
{:else}
  <NotFoundView />
{/if}
