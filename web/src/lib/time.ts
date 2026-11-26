/** "just now", "4 min ago", "2 h ago", "yesterday", "5 d ago"; the raw string if unparsable. */
export function ago(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) {
    return iso;
  }
  const minutes = Math.max(0, Math.floor((now - at) / 60_000));
  if (minutes < 1) {
    return "just now";
  }
  if (minutes < 60) {
    return minutes + " min ago";
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return hours + " h ago";
  }
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : days + " d ago";
}

/** 1536 → "1.5 KB"; decimal units, as file managers show them. */
export function formatBytes(bytes: number): string {
  if (bytes < 1000) {
    return bytes + " B";
  }
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1000;
  let i = 0;
  while (value >= 1000 && i < units.length - 1) {
    value /= 1000;
    i++;
  }
  return (value < 10 ? value.toFixed(1) : Math.round(value)) + " " + units[i];
}
