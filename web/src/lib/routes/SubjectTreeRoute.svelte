<script lang="ts">
  import { route } from "../router";
  import NotFoundView from "../views/NotFoundView.svelte";
  import TreeView from "../views/TreeView.svelte";

  // sv-router already decodes :subject once via decodeURIComponent in matchRoute.
  const subject = $derived(route.params.subject || null);
</script>

{#if subject !== null}
  <TreeView {subject} />
{:else}
  <NotFoundView />
{/if}
