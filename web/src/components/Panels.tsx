import { Track, type LocalParticipant, type RemoteParticipant } from "livekit-client";
import { useEffect, useRef, useState, type FormEvent } from "react";

import type { LobbyEntry, MuteSource } from "../api";
import { fr, t } from "../fr";
import { Icon } from "./Icon";
import { nameOf, roleOf } from "./Tiles";

export interface ChatMessage {
  id: string;
  from: string;
  mine: boolean;
  text: string;
  at: number;
}

export const MAX_CHAT_LENGTH = 2000;

const clock = new Intl.DateTimeFormat("fr-FR", {
  timeZone: "Europe/Paris",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function ChatPanel({
  messages,
  failed,
  onSend,
  onClose,
}: {
  messages: ChatMessage[];
  failed: boolean;
  onSend: (text: string) => Promise<void>;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const listEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listEnd.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    setText("");
    await onSend(value);
  };

  return (
    <aside className="side-panel" aria-label={fr.chat.title} data-testid="chat-panel">
      <header className="panel-head">
        <h2>{fr.chat.title}</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label={fr.room.close}>
          <Icon name="close" size={18} />
        </button>
      </header>
      <div className="chat-list" role="log" aria-live="polite">
        {messages.length === 0 ? <p className="muted">{fr.chat.empty}</p> : null}
        {messages.map((message) => (
          <article key={message.id} className={`chat-message ${message.mine ? "mine" : ""}`}>
            <p className="chat-meta">
              <strong>{message.mine ? fr.room.you : message.from}</strong>
              <time>{clock.format(message.at)}</time>
            </p>
            <p className="chat-text">{message.text}</p>
          </article>
        ))}
        <div ref={listEnd} />
      </div>
      {failed ? (
        <p className="note warn" role="alert">
          {fr.chat.sendError}
        </p>
      ) : null}
      <form className="chat-form" onSubmit={(event) => void submit(event)}>
        <input
          type="text"
          value={text}
          maxLength={MAX_CHAT_LENGTH}
          placeholder={fr.chat.placeholder}
          aria-label={fr.chat.placeholder}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="icon-button accent" aria-label={fr.chat.send} disabled={!text.trim()}>
          <Icon name="send" size={18} />
        </button>
      </form>
    </aside>
  );
}

export interface Moderation {
  mute: (identity: string, source: MuteSource) => void;
  remove: (identity: string) => void;
}

export function PeoplePanel({
  local,
  remote,
  moderation,
  onClose,
}: {
  local: LocalParticipant;
  remote: RemoteParticipant[];
  /** Fourni seulement aux organisateurs : le serveur revérifie le rôle à chaque action. */
  moderation?: Moderation;
  onClose: () => void;
}) {
  const everyone = [local, ...remote];
  const [confirming, setConfirming] = useState<string | null>(null);
  return (
    <aside className="side-panel" aria-label={fr.room.participants} data-testid="people-panel">
      <header className="panel-head">
        <h2>{t(fr.people.title, { count: everyone.length })}</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label={fr.room.close}>
          <Icon name="close" size={18} />
        </button>
      </header>
      <ul className="people-list">
        {everyone.map((participant) => {
          const muted = !participant.isMicrophoneEnabled;
          const name = nameOf(participant);
          // On ne modère ni soi-même ni un autre organisateur (le serveur le refuse aussi).
          const moderable =
            moderation !== undefined && !participant.isLocal && roleOf(participant) !== "organizer";
          const camera = participant.getTrackPublication(Track.Source.Camera);
          const share = participant.getTrackPublication(Track.Source.ScreenShare);
          return (
            <li key={participant.identity} data-person={name} className="person">
              <div className="person-line">
                <span className="people-name">
                  {name}
                  {participant.isLocal ? ` (${fr.room.you})` : ""}
                </span>
                {roleOf(participant) === "organizer" ? (
                  <span className="badge">{fr.room.organizer}</span>
                ) : null}
                {muted ? (
                  <span className="people-muted" title={fr.people.micMuted}>
                    <Icon name="micOff" size={16} />
                    <span className="sr-only">{fr.people.micMuted}</span>
                  </span>
                ) : null}
              </div>
              {moderable && confirming !== participant.identity ? (
                <div className="person-actions">
                  <button
                    type="button"
                    className="chip"
                    disabled={muted}
                    aria-label={t(fr.people.muteMic, { name })}
                    data-testid="mute-mic"
                    onClick={() => moderation.mute(participant.identity, "microphone")}
                  >
                    {fr.people.micShort}
                  </button>
                  <button
                    type="button"
                    className="chip"
                    disabled={!camera?.track || camera.isMuted}
                    aria-label={t(fr.people.muteCamera, { name })}
                    data-testid="mute-camera"
                    onClick={() => moderation.mute(participant.identity, "camera")}
                  >
                    {fr.people.cameraShort}
                  </button>
                  {share?.track ? (
                    <button
                      type="button"
                      className="chip"
                      aria-label={t(fr.people.stopShare, { name })}
                      data-testid="mute-share"
                      onClick={() => moderation.mute(participant.identity, "screen_share")}
                    >
                      {fr.people.shareShort}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="chip danger"
                    aria-label={t(fr.people.remove, { name })}
                    data-testid="remove"
                    onClick={() => setConfirming(participant.identity)}
                  >
                    {fr.people.removeShort}
                  </button>
                </div>
              ) : null}
              {moderable && confirming === participant.identity ? (
                <div className="person-confirm" role="alertdialog">
                  <p>{t(fr.people.removeConfirm, { name })}</p>
                  <div className="person-actions">
                    <button
                      type="button"
                      className="chip danger"
                      data-testid="remove-confirm"
                      onClick={() => {
                        setConfirming(null);
                        moderation.remove(participant.identity);
                      }}
                    >
                      {fr.people.removeYes}
                    </button>
                    <button type="button" className="chip" onClick={() => setConfirming(null)}>
                      {fr.people.removeNo}
                    </button>
                  </div>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

export function LobbyPanel({
  entries,
  onAdmit,
  onDeny,
  onAdmitAll,
  onClose,
}: {
  entries: LobbyEntry[];
  onAdmit: (id: string) => void;
  onDeny: (id: string) => void;
  onAdmitAll: () => void;
  onClose: () => void;
}) {
  const waiting = entries.filter((entry) => entry.status === "waiting");
  return (
    <aside className="side-panel" aria-label={fr.room.waitingRoom} data-testid="lobby-panel">
      <header className="panel-head">
        <h2>{t(fr.lobbyPanel.title, { count: waiting.length })}</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label={fr.room.close}>
          <Icon name="close" size={18} />
        </button>
      </header>
      {waiting.length > 1 ? (
        <div className="lobby-all">
          <button type="button" className="button primary" data-testid="admit-all" onClick={onAdmitAll}>
            {fr.lobbyPanel.admitAll}
          </button>
        </div>
      ) : null}
      {entries.length === 0 ? <p className="muted pad">{fr.lobbyPanel.empty}</p> : null}
      <ul className="people-list">
        {entries.map((entry) => (
          <li key={entry.id} className="person" data-person={entry.display_name}>
            <div className="person-line">
              <span className="people-name">{entry.display_name}</span>
              {entry.status === "denied" ? (
                <span className="badge muted-badge">{fr.lobbyPanel.denied}</span>
              ) : null}
            </div>
            <div className="person-actions">
              <button
                type="button"
                className="chip"
                aria-label={t(fr.lobbyPanel.admit, { name: entry.display_name })}
                data-testid="admit"
                onClick={() => onAdmit(entry.id)}
              >
                {fr.lobbyPanel.admitShort}
              </button>
              {entry.status === "waiting" ? (
                <button
                  type="button"
                  className="chip danger"
                  aria-label={t(fr.lobbyPanel.deny, { name: entry.display_name })}
                  data-testid="deny"
                  onClick={() => onDeny(entry.id)}
                >
                  {fr.lobbyPanel.denyShort}
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
