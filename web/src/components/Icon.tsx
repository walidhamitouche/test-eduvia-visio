// Icônes en trait, dessinées ici : aucune dépendance, aucune mention d'une bibliothèque.

const PATHS = {
  mic: ["M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19v3"],
  micOff: [
    "M2 2l20 20",
    "M9 9v3a3 3 0 0 0 5.1 2.1",
    "M15 9.3V5a3 3 0 0 0-5.9-.7",
    "M19 10v2a7 7 0 0 1-.8 3.2",
    "M5 10v2a7 7 0 0 0 11.3 5.5",
    "M12 19v3",
  ],
  video: ["M22 8l-6 4 6 4V8z", "M2 6h14v12H2z"],
  videoOff: ["M2 2l20 20", "M16 12v6H6", "M2 6h8", "M2 6v12", "M22 8l-6 4"],
  screen: ["M2 3h20v14H2z", "M8 21h8", "M12 17v4"],
  chat: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  users: [
    "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2",
    "M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
    "M23 21v-2a4 4 0 0 0-3-3.9",
    "M16 3.1a4 4 0 0 1 0 7.8",
  ],
  leave: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  send: ["M22 2L11 13", "M22 2l-7 20-4-9-9-4 20-7z"],
  close: ["M18 6L6 18", "M6 6l12 12"],
  check: ["M20 6L9 17l-5-5"],
  stop: ["M6 6h12v12H6z"],
  hourglass: ["M6 2h12", "M6 22h12", "M6 2v4l6 6-6 6v4", "M18 2v4l-6 6 6 6v4"],
  speaker: ["M11 5L6 9H2v6h4l5 4V5z", "M15.5 8.5a5 5 0 0 1 0 7", "M19 5a10 10 0 0 1 0 14"],
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 22 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
