import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  isAdmitted,
  join,
  preview as fetchPreview,
  type JoinResult,
  type Preview,
} from "./api";
import { readBrand } from "./brand";
import { fr, t } from "./fr";
import { Notice } from "./screens/Notice";
import { Lobby } from "./screens/Lobby";
import { PreJoin } from "./screens/PreJoin";
import { Room } from "./screens/Room";
import { Waiting } from "./screens/Waiting";
import { getClientId, saveClientId, saveName } from "./storage";
import { parisTime } from "./time";
import type { MediaPrefs, RoomEnd } from "./useMeetingRoom";

type Screen =
  | { kind: "loading" }
  | { kind: "error"; code: string }
  | { kind: "preview"; preview: Preview; clockOffset: number }
  | {
      kind: "lobby";
      preview: Preview;
      name: string | null;
      clientId: string | null;
      prefs: MediaPrefs;
    }
  | { kind: "denied"; preview: Preview }
  | {
      kind: "room";
      preview: Preview;
      session: JoinResult;
      name: string | null;
      clientId: string | null;
      prefs: MediaPrefs;
    }
  | { kind: "over"; preview: Preview | null; reason: RoomEnd };

const brand = readBrand();

/** Le jeton du lien : la dernière partie de `/j/<jeton>`. */
function tokenFromLocation(): string {
  const match = window.location.pathname.match(/^\/j\/([^/]+)\/?$/);
  return match?.[1] ? decodeURIComponent(match[1]) : "";
}

export function App() {
  const token = useRef(tokenFromLocation()).current;
  const [screen, setScreen] = useState<Screen>({ kind: "loading" });
  const [joining, setJoining] = useState(false);
  const [joinError, setJoinError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const preview = await fetchPreview(token);
      const clockOffset = new Date(preview.server_time).getTime() - Date.now();
      setScreen((current) =>
        current.kind === "room" ? current : { kind: "preview", preview, clockOffset },
      );
    } catch (error) {
      const code = error instanceof ApiError ? error.code : "unknown";
      setScreen((current) => (current.kind === "room" ? current : { kind: "error", code }));
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const title =
      screen.kind === "preview" || screen.kind === "room"
        ? `${screen.preview.meeting.title} – ${brand.name}`
        : brand.name;
    document.title = title;
  }, [screen]);

  const enter = async (preview: Preview, name: string | null, prefs: MediaPrefs) => {
    setJoining(true);
    setJoinError(null);
    try {
      const clientId = getClientId();
      const session = await join(token, name, clientId);
      if (session.client_id) saveClientId(session.client_id);
      if (name) saveName(name);
      const nextClientId = session.client_id ?? clientId;
      if (isAdmitted(session)) {
        setScreen({ kind: "room", preview, session, name, clientId: nextClientId, prefs });
      } else if (session.status === "denied") {
        setScreen({ kind: "denied", preview });
      } else {
        // Salle d'attente : aucun jeton, la page attend l'admission.
        setScreen({ kind: "lobby", preview, name, clientId: nextClientId, prefs });
      }
    } catch (error) {
      if (error instanceof ApiError) {
        if (["not_open_yet", "meeting_ended", "meeting_cancelled", "invalid_link"].includes(error.code)) {
          await load();
        } else if (error.code === "participant_excluded") {
          setScreen({ kind: "error", code: error.code });
        } else if (error.code === "display_name_required") {
          setJoinError(fr.prejoin.nameRequired);
        } else {
          setJoinError(error.code === "network" ? fr.network.body : fr.unknown.body);
        }
      } else {
        setJoinError(fr.unknown.body);
      }
    } finally {
      setJoining(false);
    }
  };

  const retry = () => {
    setScreen({ kind: "loading" });
    void load();
  };

  switch (screen.kind) {
    case "loading":
      return (
        <main className="page">
          <p className="connecting" role="status">
            {fr.loading}
          </p>
        </main>
      );

    case "error": {
      if (screen.code === "invalid_link") return <Notice brand={brand} {...fr.invalidLink} />;
      if (screen.code === "participant_excluded") return <Notice brand={brand} {...fr.excluded} />;
      if (screen.code === "network") {
        return <Notice brand={brand} {...fr.network} action={{ label: fr.retry, onClick: retry }} />;
      }
      return <Notice brand={brand} {...fr.unknown} action={{ label: fr.retry, onClick: retry }} />;
    }

    case "preview": {
      const { preview, clockOffset } = screen;
      const meeting = preview.meeting.title;
      if (preview.status === "cancelled") {
        return <Notice brand={brand} meeting={meeting} {...fr.cancelled} />;
      }
      if (preview.status === "ended") {
        return (
          <Notice
            brand={brand}
            meeting={meeting}
            title={fr.ended.title}
            body={t(fr.ended.body, { time: parisTime(preview.meeting.ends_at) })}
          />
        );
      }
      if (preview.status === "not_open_yet") {
        return <Waiting brand={brand} preview={preview} clockOffset={clockOffset} onRecheck={() => void load()} />;
      }
      return (
        <PreJoin
          brand={brand}
          preview={preview}
          busy={joining}
          error={joinError}
          onJoin={(name, prefs) => void enter(preview, name, prefs)}
        />
      );
    }

    case "lobby":
      return (
        <Lobby
          brand={brand}
          preview={screen.preview}
          linkToken={token}
          name={screen.name}
          clientId={screen.clientId}
          onAdmitted={(session, clientId) =>
            setScreen({
              kind: "room",
              preview: screen.preview,
              session,
              name: screen.name,
              clientId,
              prefs: screen.prefs,
            })
          }
          onDenied={() => setScreen({ kind: "denied", preview: screen.preview })}
          onGone={retry}
          onCancel={retry}
        />
      );

    case "denied":
      return <Notice brand={brand} meeting={screen.preview.meeting.title} {...fr.denied} />;

    case "room":
      return (
        <Room
          brand={brand}
          linkToken={token}
          session={screen.session}
          displayName={screen.name}
          clientId={screen.clientId}
          prefs={screen.prefs}
          onEnd={(reason) =>
            setScreen({ kind: "over", preview: screen.preview, reason })
          }
        />
      );

    case "over": {
      const meeting = screen.preview?.meeting.title;
      const back = (label: string) => ({ label, onClick: retry });
      switch (screen.reason) {
        case "removed":
          return <Notice brand={brand} meeting={meeting} {...fr.removed} />;
        case "excluded":
          return <Notice brand={brand} meeting={meeting} {...fr.excluded} />;
        case "ended":
          return (
            <Notice
              brand={brand}
              meeting={meeting}
              title={fr.ended.title}
              body={t(fr.ended.body, {
                time: parisTime(screen.preview?.meeting.ends_at ?? new Date().toISOString()),
              })}
            />
          );
        case "duplicate":
          return (
            <Notice
              brand={brand}
              meeting={meeting}
              title={fr.duplicate.title}
              body={fr.duplicate.body}
              action={back(fr.duplicate.back)}
            />
          );
        case "failed":
          return (
            <Notice
              brand={brand}
              meeting={meeting}
              title={fr.failed.title}
              body={fr.failed.body}
              action={back(fr.failed.back)}
            />
          );
        default:
          return (
            <Notice
              brand={brand}
              meeting={meeting}
              title={fr.left.title}
              body={fr.left.body}
              action={back(fr.left.back)}
            />
          );
      }
    }
  }
}
