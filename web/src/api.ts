// Appels au backend. Les erreurs arrivent toujours sous la forme
// {"error": {"code", "message"}} (voir docs/DECISIONS.md §3).

export type Role = "organizer" | "participant";
export type SlotStatus = "not_open_yet" | "open" | "ended" | "cancelled";

export interface MeetingInfo {
  title: string;
  starts_at: string;
  ends_at: string;
  waiting_room: boolean;
  recording: boolean;
  transcription: boolean;
}

export interface Preview {
  status: SlotStatus;
  server_time: string;
  opens_at: string;
  meeting: MeetingInfo;
  participant: {
    role: Role;
    display_name: string | null;
    external_id: string | null;
    name_required: boolean;
  };
}

export type Admission = "admitted" | "waiting" | "denied";

/** Réponse de `POST /v1/join` : sans jeton tant que la personne attend ou est refusée. */
export interface JoinResponse {
  status: Admission;
  livekit_url: string | null;
  livekit_token: string | null;
  expires_at: string | null;
  seconds_until_end: number;
  client_id: string | null;
  meeting: MeetingInfo;
  participant: { display_name: string; external_id: string | null; role: Role };
}

/** Une entrée acceptée : le jeton de salle est présent. */
export interface JoinResult extends JoinResponse {
  status: "admitted";
  livekit_url: string;
  livekit_token: string;
  expires_at: string;
}

export const isAdmitted = (response: JoinResponse): response is JoinResult =>
  response.status === "admitted" && Boolean(response.livekit_token);

export interface LobbyEntry {
  id: string;
  display_name: string;
  external_id: string | null;
  status: "waiting" | "denied";
  requested_at: string;
}

export interface ChatMessageOut {
  id: string;
  author: { display_name: string; external_id: string | null; role: Role };
  sent_at: string;
  text: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly opensAt?: string,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  } catch {
    throw new ApiError(0, "network", "réseau inaccessible");
  }
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    // corps absent ou illisible : traité plus bas
  }
  if (!response.ok) {
    const error = (data as { error?: { code?: string; message?: string; opens_at?: string } } | null)
      ?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "unknown",
      error?.message ?? `erreur ${response.status}`,
      error?.opens_at,
    );
  }
  return data as T;
}

export const preview = (token: string): Promise<Preview> =>
  post<Preview>("/v1/join/preview", { token });

export const join = (token: string, displayName: string | null, clientId: string | null) => {
  const body: Record<string, string> = { token };
  if (displayName) body.display_name = displayName;
  if (clientId) body.client_id = clientId;
  return post<JoinResponse>("/v1/join", body);
};

// --- Salle : l'autorisation est vérifiée par le serveur avec le jeton du lien ---------------

export type MuteSource = "microphone" | "camera" | "screen_share";

export const muteParticipant = (token: string, identity: string, source: MuteSource) =>
  post<{ muted: number }>("/v1/room/mute", { token, identity, source });

export const removeParticipant = (token: string, identity: string) =>
  post<{ ok: boolean }>("/v1/room/remove", { token, identity });

export const endMeeting = (token: string) => post<{ ok: boolean }>("/v1/room/end", { token });

export const fetchLobby = (token: string) =>
  post<{ entries: LobbyEntry[] }>("/v1/room/lobby", { token });

export const admitEntries = (token: string, ids: string[]) =>
  post<{ count: number }>("/v1/room/lobby/admit", { token, ids });

export const denyEntries = (token: string, ids: string[]) =>
  post<{ count: number }>("/v1/room/lobby/deny", { token, ids });

export const admitAll = (token: string) =>
  post<{ count: number }>("/v1/room/lobby/admit-all", { token });

export const sendChatMessage = (token: string, text: string, clientId: string | null) => {
  const body: Record<string, string> = { token, text };
  if (clientId) body.client_id = clientId;
  return post<ChatMessageOut>("/v1/room/chat", body);
};
