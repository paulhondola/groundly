<script lang="ts">
  import Button from "../components/ui/Button.svelte";
  import Card from "../components/ui/Card.svelte";
  import { getMalformedPathname, route } from "../router";

  interface Props {
    pathname?: string;
  }

  let { pathname }: Props = $props();
  const displayPath = $derived(pathname ?? getMalformedPathname() ?? route.pathname);
</script>

<Card class="mx-auto max-w-md space-y-3 p-6">
  <h1 class="text-lg font-semibold text-foreground">Page not found</h1>
  <p class="text-sm text-muted-foreground">
    No view exists for <code class="font-mono text-foreground">{displayPath}</code>.
  </p>
  <Button href="/" size="sm">All subjects</Button>
</Card>
