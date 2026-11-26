<script lang="ts">
  import { hostLine, useActivity } from "../activity.svelte";
  import Orb from "./orb/Orb.svelte";

  const activity = useActivity();

  const working = $derived(
    activity.mode === "reading" || activity.mode === "mapping" || activity.mode === "writing",
  );
  const text = $derived(
    activity.available
      ? activity.mode === "done"
        ? "Done"
        : hostLine(activity.hosts)
      : "Connections",
  );
</script>

<a
  href="/connections"
  aria-label={"Connections: " + text}
  class="flex h-10 max-w-[18rem] min-w-0 items-center gap-2.5 rounded-full border border-border bg-card pr-3.5 pl-3 text-[13px] text-muted-foreground hover:border-ring/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
>
  <Orb mode={activity.available ? activity.mode : "idle"} size={16} />
  <span class={working ? "shimmer truncate" : "truncate"}>{text}</span>
</a>
