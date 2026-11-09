// Connexion à la salle et reconnexions.
//
// Le jeton de salle ne dure que quelques minutes (docs/DECISIONS.md, point 31) : toute
// reconnexion repasse par `POST /v1/join`, qui revérifie le lien, le créneau et l'exclusion.
// Le serveur reste donc seul juge : une réunion terminée ou un lien révoqué ne revient pas.

import {
  DisconnectReason,
  Room,
  RoomEvent,
  Track,
  VideoPresets,
  type RoomOptions,
} from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, isAdmitted, join, type JoinResult } from "./api";
import { saveClientId } from "./storage";

export interface MediaPrefs {
  micOn: boolean;
  camOn: boolean;
  micId: string | null;
  camId: string | null;
}

export type RoomPhase = "connecting" | "connected" | "reconnecting";
export type RoomEnd = "left" | "removed" | "ended" | "excluded" | "duplicate" | "failed";

interface Options {
  linkToken: string;
  initial: JoinResult;
  displayName: string | null;
  clientId: string | null;
  prefs: MediaPrefs;
  onEnd: (reason: RoomEnd) => void;
}

const MAX_ATTEMPTS = 6;
const EVENTS = [
  RoomEvent.ParticipantConnected,
  RoomEvent.ParticipantDisconnected,
  RoomEvent.TrackPublished,
  RoomEvent.TrackUnpublished,
  RoomEvent.TrackSubscribed,
  RoomEvent.TrackUnsubscribed,
  RoomEvent.TrackMuted,
  RoomEvent.TrackUnmuted,
  RoomEvent.LocalTrackPublished,
  RoomEvent.LocalTrackUnpublished,
  RoomEvent.ActiveSpeakersChanged,
  RoomEvent.ParticipantNameChanged,
  RoomEvent.ParticipantMetadataChanged,
  RoomEvent.ConnectionStateChanged,
  RoomEvent.RecordingStatusChanged,
  RoomEvent.AudioPlaybackStatusChanged,
] as const;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function useMeetingRoom(options: Options) {
  const optionsRef = useRef(options);
  const [room, setRoom] = useState<Room | null>(null);
  const [phase, setPhase] = useState<RoomPhase>("connecting");
  const [, setVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  // Ce que la personne veut (micro, caméra) : rétabli après une reconnexion.
  const wanted = useRef({ mic: options.prefs.micOn, cam: options.prefs.camOn });
  const stop = useRef<() => void>(() => undefined);

  useEffect(() => {
    let cancelled = false;
    let userLeft = false;
    let current: Room | null = null;
    const { linkToken, displayName, clientId, prefs, onEnd } = optionsRef.current;
    let session: JoinResult = optionsRef.current.initial;

    stop.current = () => {
      userLeft = true;
      void current?.disconnect();
    };
    
    const rawAudio = localStorage.getItem("audioRaw") === "1";
    // Diagnostic réseau (étape 18) : `?relais=1` force le passage par le TURN (aucun trafic direct).
    const forceRelay = new URLSearchParams(window.location.search).get("relais") === "1";
    const createRoom = (): Room => {
      const roomOptions: RoomOptions = {
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: {
          deviceId: prefs.micId ?? undefined,
          echoCancellation: !rawAudio,
          noiseSuppression: !rawAudio,
          autoGainControl: !rawAudio,
        },
        videoCaptureDefaults: {
          deviceId: prefs.camId ?? undefined,
          resolution: VideoPresets.h720.resolution,
        },
      };
      const created = new Room(roomOptions);
      const refresh = () => setVersion((value) => value + 1);
      for (const event of EVENTS) created.on(event, refresh);
      return created;
    };

    const enableDevices = async (connected: Room) => {
      const attempts: [boolean, () => Promise<unknown>][] = [
        [wanted.current.mic, () => connected.localParticipant.setMicrophoneEnabled(true)],
        [wanted.current.cam, () => connected.localParticipant.setCameraEnabled(true)],
      ];
      for (const [shouldEnable, enable] of attempts) {
        if (!shouldEnable) continue;
        try {
          await enable();
        } catch {
          // refus d'autorisation ou appareil absent : on entre quand même, sans cet appareil
        }
      }
    };

    const waitForDisconnect = (connected: Room) =>
      new Promise<DisconnectReason | undefined>((resolve) => {
        connected.once(RoomEvent.Disconnected, (reason) => resolve(reason));
      });

    // Un organisateur coupe notre partage d'écran : le serveur de médias le gèle pour tout le
    // monde, et ici on l'arrête pour de bon (sinon le navigateur resterait en « partage »).
    const stopShareWhenMuted = (publication: { source: Track.Source }, participant: unknown) => {
      if (publication.source === Track.Source.ScreenShare && participant === current?.localParticipant) {
        void current?.localParticipant.setScreenShareEnabled(false);
      }
    };

    const run = async () => {
      let attempt = 0;
      let needFreshToken = false; // le premier tour utilise le jeton déjà obtenu
      while (!cancelled) {
        try {
          if (needFreshToken) {
            const fresh = await join(linkToken, displayName, clientId);
            if (fresh.client_id) saveClientId(fresh.client_id);
            // Plus admis (ne devrait pas arriver) : pas de jeton, on ne s'obstine pas.
            if (!isAdmitted(fresh)) return onEnd("excluded");
            session = fresh;
          }
          needFreshToken = true;
          const next = createRoom();
          current = next;
          next.on(RoomEvent.TrackMuted, stopShareWhenMuted);
          await next.connect(
            session.livekit_url,
            session.livekit_token,
            forceRelay ? { rtcConfig: { iceTransportPolicy: "relay" } } : undefined,
          );
          if (cancelled || userLeft) {
            void next.disconnect();
            return;
          }
          setRoom(next);
          setPhase("connected");
          attempt = 0;
          // Écouter la déconnexion AVANT d'allumer les appareils : une fin de réunion pendant ce
          // temps ne doit pas être manquée.
          const disconnected = waitForDisconnect(next);
          await enableDevices(next);
          setVersion((value) => value + 1);

          const reason = await disconnected;
          if (cancelled || userLeft) return;
          if (reason === DisconnectReason.PARTICIPANT_REMOVED) return onEnd("removed");
          if (reason === DisconnectReason.DUPLICATE_IDENTITY) return onEnd("duplicate");
          setPhase("reconnecting");
        } catch (error) {
          if (cancelled || userLeft) return;
          if (error instanceof ApiError) {
            if (["meeting_ended", "meeting_cancelled", "not_open_yet"].includes(error.code)) {
              return onEnd("ended");
            }
            if (error.code === "participant_excluded") return onEnd("excluded");
          }
          attempt += 1;
          if (attempt >= MAX_ATTEMPTS) return onEnd("failed");
          setPhase("reconnecting");
          await sleep(Math.min(1500 * attempt, 8000));
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
      void current?.disconnect();
    };
  }, []);

  const leave = useCallback(() => {
    stop.current();
    optionsRef.current.onEnd("left");
  }, []);

  const setMicrophone = useCallback(
    async (enabled: boolean) => {
      wanted.current.mic = enabled;
      try {
        await room?.localParticipant.setMicrophoneEnabled(enabled);
        setNotice(null);
      } catch {
        wanted.current.mic = false;
        setNotice("microphone");
      }
      setVersion((value) => value + 1);
    },
    [room],
  );

  const setCamera = useCallback(
    async (enabled: boolean) => {
      wanted.current.cam = enabled;
      try {
        await room?.localParticipant.setCameraEnabled(enabled);
        setNotice(null);
      } catch {
        wanted.current.cam = false;
        setNotice("camera");
      }
      setVersion((value) => value + 1);
    },
    [room],
  );

  const setScreenShare = useCallback(
    async (enabled: boolean) => {
      try {
        await room?.localParticipant.setScreenShareEnabled(enabled, { audio: true });
        setNotice(null);
      } catch (error) {
        // Fermer la fenêtre de choix du navigateur n'est pas une erreur.
        const cancelledByUser = error instanceof DOMException && error.name === "NotAllowedError";
        setNotice(cancelledByUser ? null : "share");
      }
      setVersion((value) => value + 1);
    },
    [room],
  );

  return { room, phase, notice, leave, setMicrophone, setCamera, setScreenShare };
}
