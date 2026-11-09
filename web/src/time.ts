// Affichage des dates : toujours l'heure de Paris, quel que soit le fuseau de l'appareil.

const ZONE = "Europe/Paris";

const timeFormat = new Intl.DateTimeFormat("fr-FR", {
  timeZone: ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const dateFormat = new Intl.DateTimeFormat("fr-FR", {
  timeZone: ZONE,
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});

export const parisTime = (iso: string): string => timeFormat.format(new Date(iso));
export const parisDate = (iso: string): string => dateFormat.format(new Date(iso));

/** « 2 j 03 h 15 min », « 12 min 05 s », « 42 s » : le plus grand nombre d'unités utile. */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  if (days > 0) return `${days} j ${two(hours)} h ${two(minutes)} min`;
  if (hours > 0) return `${hours} h ${two(minutes)} min ${two(rest)} s`;
  if (minutes > 0) return `${minutes} min ${two(rest)} s`;
  return `${rest} s`;
}
