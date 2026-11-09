import { RoomEvent, type RemoteParticipant } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  admitAll,
  admitEntries,
  denyEntries,
  endMeeting,
  fetchLobby,
  muteParticipant,
  removeParticipant,
  sendChatMessage,
  type JoinResult,
  type LobbyEntry,
  type MuteSource,
} from "../api";
import type { Brand } from "../brand";
import { BrandBar } from "../components/BrandBar";
import { Icon, type IconName } from "../components/Icon";
import {
  ChatPanel,
  LobbyPanel,
  MAX_CHAT_LENGTH,
  PeoplePanel,
  type ChatMessage,
} from "../components/Panels";
import {
  ParticipantTile,
  RemoteAudio,
  ScreenStage,
  hasScreenShare,
} from "../components/Tiles";
import { fr, t } from "../fr";
import { formatDuration, parisTime } from "../time";
import { useMeetingRoom, type MediaPrefs, type RoomEnd } from "../useMeetingRoom";

interface Props {
  brand: Brand;
  linkToken: string;
  session: JoinResult;
  displayName: string | null;
  clientId: string | null;
  prefs: MediaPrefs;
  onEnd: (reason: RoomEnd) => void;
}

type Panel = "chat" | "people" | "lobby" | null;

const LOBBY_POLL_MS = 3000;
const WARN_AT_SECONDS = 300; // avertissement visible à 5 min de la fin, puis compte à rebours
const URGENT_AT_SECONDS = 60;

const decoder = new TextDecoder();

function ControlButton(props: {
  icon: IconName;
  label: string;
  pressed?: boolean;
  off?: boolean;
  danger?: boolean;
  badge?: number;
  testId?: string;
  onClick: () => void;
}) {
  const { icon, label, pressed, off, danger, badge, testId, onClick } = props;
  return (
    <button
      type="button"
      className={`control ${off ? "is-off" : ""} ${danger ? "danger" : ""}`}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      data-testid={testId}
      onClick={onClick}
    >
      <Icon name={icon} />
      {badge ? <span className="control-badge">{badge > 9 ? "9+" : badge}</span> : null}
    </button>
  );
}

/** La salle : vidéos, partage d'écran, discussion, participants. */
export function Room({ brand, linkToken, session, displayName, clientId, prefs, onEnd }: Props) {
  const { room, phase, notice, leave, setMicrophone, setCamera, setScreenShare } = useMeetingRoom({
    linkToken,
    initial: session,
    displayName,
    clientId,
    prefs,
    onEnd,
  });
  const isOrganizer = session.participant.role === "organizer";
  const [panel, setPanel] = useState<Panel>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [unread, setUnread] = useState(0);
  const [chatFailed, setChatFailed] = useState(false);
  const [lobby, setLobby] = useState<LobbyEntry[]>([]);
  const [actionFailed, setActionFailed] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const panelRef = useRef<Panel>(null);
  panelRef.current = panel;
  const mine = useRef(new Set<string>());

  // Compte à rebours de la fin : calé sur l'heure du serveur au moment de l'entrée.
  const endsAtLocal = useRef(Date.now() + session.seconds_until_end * 1000).current;
  const [remaining, setRemaining] = useState(session.seconds_until_end);
  useEffect(() => {
    const timer = window.setInterval(
      () => setRemaining(Math.max(0, Math.ceil((endsAtLocal - Date.now()) / 1000))),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [endsAtLocal]);

  // Discussion : le serveur enregistre chaque message avec le nom vérifié de son auteur puis le
  // diffuse à la salle. On n'affiche que ces diffusions (un message venant d'un participant ne
  // serait pas du serveur : ignoré).
  useEffect(() => {
    if (!room) return undefined;
    const onData = (payload: Uint8Array, participant?: RemoteParticipant, _kind?: unknown, topic?: string) => {
      if (topic !== "chat" || participant) return;
      try {
        const data = JSON.parse(decoder.decode(payload)) as {
          id?: unknown;
          from?: unknown;
          text?: unknown;
          at?: unknown;
        };
        if (typeof data.id !== "string" || typeof data.text !== "string" || !data.text.trim()) return;
        const id = data.id;
        const message: ChatMessage = {
          id,
          from: typeof data.from === "string" ? data.from : "?",
          mine: mine.current.has(id),
          text: data.text.slice(0, MAX_CHAT_LENGTH),
          at: typeof data.at === "string" ? new Date(data.at).getTime() : Date.now(),
        };
        setMessages((list) => (list.some((item) => item.id === id) ? list : [...list, message]));
        if (panelRef.current !== "chat" && !message.mine) setUnread((count) => count + 1);
      } catch {
        // message mal formé : ignoré
      }
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
    };
  }, [room]);

  const sendChat = useCallback(
    async (text: string) => {
      try {
        const saved = await sendChatMessage(linkToken, text, clientId);
        mine.current.add(saved.id);
        setChatFailed(false);
        // La diffusion peut arriver avant la réponse : on marque alors le message comme le nôtre.
        setMessages((list) =>
          list.some((item) => item.id === saved.id)
            ? list.map((item) => (item.id === saved.id ? { ...item, mine: true } : item))
            : [
                ...list,
                {
                  id: saved.id,
                  from: saved.author.display_name,
                  mine: true,
                  text: saved.text,
                  at: new Date(saved.sent_at).getTime(),
                },
              ],
        );
      } catch {
        setChatFailed(true);
      }
    },
    [linkToken, clientId],
  );

  // Salle d'attente : l'organisateur relit la liste toutes les 3 s (seulement si elle est active).
  const lobbyActive = isOrganizer && session.meeting.waiting_room;
  const refreshLobby = useCallback(async () => {
    try {
      setLobby((await fetchLobby(linkToken)).entries);
    } catch {
      // coupure passagère : la liste se rafraîchit au prochain tour
    }
  }, [linkToken]);
  useEffect(() => {
    if (!lobbyActive) return undefined;
    void refreshLobby();
    const timer = window.setInterval(() => void refreshLobby(), LOBBY_POLL_MS);
    return () => window.clearInterval(timer);
  }, [lobbyActive, refreshLobby]);

  // Actions de l'organisateur : le serveur revérifie le rôle, l'interface ne fait que demander.
  const run = useCallback(async (action: () => Promise<unknown>) => {
    try {
      await action();
      setActionFailed(false);
    } catch {
      setActionFailed(true);
    }
  }, []);
  const moderation = {
    mute: (identity: string, source: MuteSource) =>
      void run(() => muteParticipant(linkToken, identity, source)),
    remove: (identity: string) => void run(() => removeParticipant(linkToken, identity)),
  };
  const decide = (action: () => Promise<unknown>) =>
    void run(async () => {
      await action();
      await refreshLobby();
    });

  const waitingCount = lobby.filter((entry) => entry.status === "waiting").length;
  const endWarning = remaining > 0 && remaining <= WARN_AT_SECONDS;
  const urgent = remaining <= URGENT_AT_SECONDS;

  const openPanel = (next: Exclude<Panel, null>) => {
    setPanel((current) => (current === next ? null : next));
    if (next === "chat") setUnread(0);
  };

  const remote = room ? Array.from(room.remoteParticipants.values()) : [];
  const local = room?.localParticipant ?? null;
  const everyone = local ? [local, ...remote] : remote;
  const sharer = everyone.find(hasScreenShare) ?? null;
  const canShare = typeof navigator.mediaDevices?.getDisplayMedia === "function";
  const micOn = local?.isMicrophoneEnabled ?? false;
  const camOn = local?.isCameraEnabled ?? false;
  const sharing = local?.isScreenShareEnabled ?? false;
  const recording = room?.isRecording ?? false;

  const noticeText = notice
    ? notice === "share"
      ? fr.room.shareError
      : t(fr.room.mediaError, {
          device: notice === "camera" ? fr.room.devices.camera : fr.room.devices.microphone,
        })
    : null;

  return (
    <div className="room" data-testid="room">
      <header className="room-top">
        <BrandBar brand={brand} />
        <div className="room-title">
          <strong>{session.meeting.title}</strong>
          <span className="muted">{t(fr.room.endsAt, { time: parisTime(session.meeting.ends_at) })}</span>
        </div>
        {recording ? (
          <span className="recording" role="status" data-testid="recording-indicator">
            <span className="dot" aria-hidden="true" />
            {fr.room.recording}
          </span>
        ) : null}
      </header>

      {phase === "reconnecting" ? (
        <p className="banner" role="status">
          {fr.room.reconnecting}
        </p>
      ) : null}
      {room && !room.canPlaybackAudio ? (
        <button type="button" className="banner action" onClick={() => void room.startAudio()}>
          {fr.room.enableSound}
        </button>
      ) : null}
      {noticeText ? (
        <p className="banner warn" role="alert">
          {noticeText}
        </p>
      ) : null}
      {actionFailed ? (
        <p className="banner warn" role="alert">
          {fr.room.moderationError}
        </p>
      ) : null}
      {endWarning ? (
        <p
          className={`banner end-warning ${urgent ? "warn" : ""}`}
          role="status"
          data-testid="end-warning"
        >
          {t(urgent ? fr.room.endWarningNow : fr.room.endWarning, {
            duration: formatDuration(remaining),
          })}
        </p>
      ) : null}

      <div className="room-body">
        <main className={`media ${sharer ? "with-stage" : ""}`}>
          {!room ? (
            <p className="connecting" role="status">
              {fr.room.connecting}
            </p>
          ) : (
            <>
              {sharer ? <ScreenStage participant={sharer} /> : null}
              <ul className={`tiles ${sharer ? "rail" : ""}`} data-count={everyone.length}>
                {everyone.map((participant) => (
                  <ParticipantTile
                    key={participant.identity}
                    participant={participant}
                    compact={Boolean(sharer)}
                  />
                ))}
              </ul>
              <RemoteAudio participants={remote} />
            </>
          )}
        </main>

        {panel === "chat" ? (
          <ChatPanel
            messages={messages}
            failed={chatFailed}
            onSend={sendChat}
            onClose={() => setPanel(null)}
          />
        ) : null}
        {panel === "people" && local ? (
          <PeoplePanel
            local={local}
            remote={remote}
            moderation={isOrganizer ? moderation : undefined}
            onClose={() => setPanel(null)}
          />
        ) : null}
        {panel === "lobby" && lobbyActive ? (
          <LobbyPanel
            entries={lobby}
            onAdmit={(id) => decide(() => admitEntries(linkToken, [id]))}
            onDeny={(id) => decide(() => denyEntries(linkToken, [id]))}
            onAdmitAll={() => decide(() => admitAll(linkToken))}
            onClose={() => setPanel(null)}
          />
        ) : null}
      </div>

      <footer className="controls">
        <ControlButton
          icon={micOn ? "mic" : "micOff"}
          label={micOn ? fr.room.micMute : fr.room.micUnmute}
          pressed={micOn}
          off={!micOn}
          testId="toggle-mic"
          onClick={() => void setMicrophone(!micOn)}
        />
        <ControlButton
          icon={camOn ? "video" : "videoOff"}
          label={camOn ? fr.room.camMute : fr.room.camUnmute}
          pressed={camOn}
          off={!camOn}
          testId="toggle-camera"
          onClick={() => void setCamera(!camOn)}
        />
        {canShare ? (
          <ControlButton
            icon="screen"
            label={sharing ? fr.room.shareStop : fr.room.shareStart}
            pressed={sharing}
            testId="toggle-share"
            onClick={() => void setScreenShare(!sharing)}
          />
        ) : null}
        <ControlButton
          icon="chat"
          label={fr.room.chat}
          pressed={panel === "chat"}
          badge={unread}
          testId="toggle-chat"
          onClick={() => openPanel("chat")}
        />
        <ControlButton
          icon="users"
          label={fr.room.participants}
          pressed={panel === "people"}
          testId="toggle-people"
          onClick={() => openPanel("people")}
        />
        {lobbyActive ? (
          <ControlButton
            icon="hourglass"
            label={fr.room.waitingRoom}
            pressed={panel === "lobby"}
            badge={waitingCount}
            testId="toggle-lobby"
            onClick={() => openPanel("lobby")}
          />
        ) : null}
        {isOrganizer ? (
          <ControlButton
            icon="stop"
            label={fr.room.endMeeting}
            danger
            testId="end-meeting"
            onClick={() => setConfirmEnd(true)}
          />
        ) : null}
        <ControlButton icon="leave" label={fr.room.leave} danger testId="leave" onClick={leave} />
      </footer>

      {confirmEnd ? (
        <div className="dialog-backdrop">
          <div className="dialog" role="alertdialog" aria-labelledby="end-title" data-testid="end-dialog">
            <h2 id="end-title">{fr.room.endConfirmTitle}</h2>
            <p>{fr.room.endConfirmBody}</p>
            <div className="dialog-actions">
              <button type="button" className="button" onClick={() => setConfirmEnd(false)}>
                {fr.room.cancel}
              </button>
              <button
                type="button"
                className="button danger"
                data-testid="end-confirm"
                onClick={() => {
                  setConfirmEnd(false);
                  void run(() => endMeeting(linkToken));
                }}
              >
                {fr.room.endConfirm}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
