import {
  Track,
  type LocalParticipant,
  type Participant,
  type RemoteParticipant,
  type TrackPublication,
} from "livekit-client";
import { useEffect, useRef } from "react";

import { fr } from "../fr";
import { Icon } from "./Icon";

type AnyParticipant = LocalParticipant | RemoteParticipant;

export const nameOf = (participant: Participant): string =>
  participant.name?.trim() || participant.identity;

export function roleOf(participant: Participant): string | null {
  try {
    const data = JSON.parse(participant.metadata || "{}") as { role?: string };
    return data.role ?? null;
  } catch {
    return null;
  }
}

const initials = (name: string): string =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("") || "?";

function VideoView({ publication, contain }: { publication: TrackPublication; contain?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  const track = publication.track;
  useEffect(() => {
    const element = ref.current;
    if (!element || !track) return undefined;
    track.attach(element);
    return () => {
      track.detach(element);
    };
  }, [track]);
  return <video ref={ref} className={contain ? "contain" : undefined} autoPlay playsInline muted />;
}

function AudioSink({ publication }: { publication: TrackPublication }) {
  const ref = useRef<HTMLAudioElement>(null);
  const track = publication.track;
  useEffect(() => {
    const element = ref.current;
    if (!element || !track) return undefined;
    track.attach(element);
    return () => {
      track.detach(element);
    };
  }, [track]);
  return <audio ref={ref} autoPlay />;
}

/** Le son des autres participants (micro et son du partage d'écran). */
export function RemoteAudio({ participants }: { participants: RemoteParticipant[] }) {
  return (
    <div hidden>
      {participants.flatMap((participant) =>
        [Track.Source.Microphone, Track.Source.ScreenShareAudio].flatMap((source) => {
          const publication = participant.getTrackPublication(source);
          return publication?.track ? [<AudioSink key={publication.trackSid} publication={publication} />] : [];
        }),
      )}
    </div>
  );
}

interface TileProps {
  participant: AnyParticipant;
  /** Vignette plus petite (colonne latérale pendant un partage d'écran). */
  compact?: boolean;
}

/** Une personne : sa caméra, ou ses initiales quand elle est coupée. */
export function ParticipantTile({ participant, compact }: TileProps) {
  const camera = participant.getTrackPublication(Track.Source.Camera);
  const microphone = participant.getTrackPublication(Track.Source.Microphone);
  const cameraOn = Boolean(camera?.track) && !camera?.isMuted;
  const micMuted = !microphone || microphone.isMuted;
  const name = nameOf(participant);
  const className = [
    "tile",
    compact ? "compact" : "",
    participant.isSpeaking ? "speaking" : "",
    participant.isLocal ? "local" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <li className={className} data-testid="tile" data-name={name}>
      {cameraOn && camera ? (
        <VideoView publication={camera} />
      ) : (
        <div className="avatar" aria-label={fr.room.cameraOff}>
          <span aria-hidden="true">{initials(name)}</span>
        </div>
      )}
      <div className="tile-label">
        {micMuted ? (
          <span className="tile-muted" title={fr.room.micMuted}>
            <Icon name="micOff" size={14} />
            <span className="sr-only">{fr.room.micMuted}</span>
          </span>
        ) : null}
        <span className="tile-name">{name}</span>
        {participant.isLocal ? <span className="tile-you">({fr.room.you})</span> : null}
      </div>
    </li>
  );
}

/** Écran partagé, en grand. */
export function ScreenStage({ participant }: { participant: AnyParticipant }) {
  const share = participant.getTrackPublication(Track.Source.ScreenShare);
  if (!share?.track) return null;
  return (
    <div className="stage" data-testid="screen-share">
      <VideoView publication={share} contain />
      <p className="stage-label">{fr.room.sharing.replace("{name}", nameOf(participant))}</p>
    </div>
  );
}

export const hasScreenShare = (participant: AnyParticipant): boolean =>
  Boolean(participant.getTrackPublication(Track.Source.ScreenShare)?.track);
