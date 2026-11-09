import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Preview } from "../api";
import type { Brand } from "../brand";
import { BrandBar } from "../components/BrandBar";
import { Icon } from "../components/Icon";
import { fr } from "../fr";
import { getSavedName } from "../storage";
import type { MediaPrefs } from "../useMeetingRoom";
import { useLocalPreview } from "../useLocalPreview";

interface Props {
  brand: Brand;
  preview: Preview;
  busy: boolean;
  error: string | null;
  onJoin: (name: string | null, prefs: MediaPrefs) => void;
}

/** Pré-jointure : nom (saisi ou verrouillé), test du micro et de la caméra. */
export function PreJoin({ brand, preview, busy, error, onJoin }: Props) {
  const { participant, meeting } = preview;
  const locked = !participant.name_required;
  const [name, setName] = useState(locked ? (participant.display_name ?? "") : getSavedName());
  const [micOn, setMicOn] = useState(true);
  const [camOn, setCamOn] = useState(true);
  const [micId, setMicId] = useState<string | null>(null);
  const [camId, setCamId] = useState<string | null>(null);
  const [nameError, setNameError] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const media = useLocalPreview({ micOn, camOn, micId, camId });

  useEffect(() => {
    const element = videoRef.current;
    if (element) element.srcObject = media.videoStream;
  }, [media.videoStream]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!locked && !trimmed) {
      setNameError(true);
      return;
    }
    onJoin(locked ? null : trimmed, {
      // Un appareil inaccessible ne doit pas empêcher d'entrer : on entre sans.
      micOn: micOn && media.micProblem === null,
      camOn: camOn && media.camProblem === null,
      micId,
      camId,
    });
  };

  const hasProblem = media.micProblem !== null || media.camProblem !== null;
  const deniedOnly = media.micProblem === "unavailable" && media.camProblem === "unavailable";

  return (
    <main className="page page-wide">
      <BrandBar brand={brand} />
      <section className="prejoin">
        <div className="prejoin-media">
          <div className="preview-frame">
            {camOn && media.videoStream ? (
              <video ref={videoRef} autoPlay muted playsInline aria-label={fr.prejoin.camera} />
            ) : (
              <p className="preview-off">{fr.prejoin.cameraOff}</p>
            )}
          </div>
          <div className="toggle-row">
            <button
              type="button"
              className={`round-button ${micOn ? "" : "is-off"}`}
              aria-pressed={micOn}
              aria-label={micOn ? fr.prejoin.micOn : fr.prejoin.micOff}
              title={micOn ? fr.prejoin.micOn : fr.prejoin.micOff}
              onClick={() => setMicOn((value) => !value)}
            >
              <Icon name={micOn ? "mic" : "micOff"} />
            </button>
            <button
              type="button"
              className={`round-button ${camOn ? "" : "is-off"}`}
              aria-pressed={camOn}
              aria-label={camOn ? fr.prejoin.camOn : fr.prejoin.camOff}
              title={camOn ? fr.prejoin.camOn : fr.prejoin.camOff}
              onClick={() => setCamOn((value) => !value)}
            >
              <Icon name={camOn ? "video" : "videoOff"} />
            </button>
            <div
              className="level"
              role="meter"
              aria-label={fr.prejoin.level}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(media.level * 100)}
            >
              <span style={{ width: `${Math.round(media.level * 100)}%` }} />
            </div>
          </div>
          {hasProblem ? (
            <p className="note warn" role="alert">
              {deniedOnly || media.micProblem === "unavailable" || media.camProblem === "unavailable"
                ? fr.prejoin.devicesUnavailable
                : fr.prejoin.devicesDenied}
            </p>
          ) : null}
        </div>

        <form className="prejoin-form" onSubmit={submit} noValidate>
          <p className="meeting-name">{meeting.title}</p>
          <h1>{fr.prejoin.title}</h1>

          <label className="field">
            <span>{fr.prejoin.name}</span>
            <input
              type="text"
              name="display_name"
              value={name}
              readOnly={locked}
              maxLength={200}
              autoComplete="name"
              placeholder={fr.prejoin.namePlaceholder}
              aria-invalid={nameError}
              aria-describedby="name-help"
              onChange={(event) => {
                setName(event.target.value);
                setNameError(false);
              }}
            />
          </label>
          <p id="name-help" className={`help ${nameError ? "error" : ""}`}>
            {nameError ? fr.prejoin.nameRequired : locked ? fr.prejoin.nameLocked : fr.prejoin.nameHelp}
          </p>

          <div className="field-row">
            <label className="field">
              <span>{fr.prejoin.microphone}</span>
              <select
                value={micId ?? ""}
                onChange={(event) => setMicId(event.target.value || null)}
                disabled={media.microphones.length === 0}
              >
                <option value="">{fr.prejoin.defaultDevice}</option>
                {media.microphones.map((device) => (
                  <option key={device.id} value={device.id}>
                    {device.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>{fr.prejoin.camera}</span>
              <select
                value={camId ?? ""}
                onChange={(event) => setCamId(event.target.value || null)}
                disabled={media.cameras.length === 0}
              >
                <option value="">{fr.prejoin.defaultDevice}</option>
                {media.cameras.map((device) => (
                  <option key={device.id} value={device.id}>
                    {device.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <ul className="notes">
            {meeting.recording ? <li>{fr.prejoin.recording}</li> : null}
            {meeting.transcription ? <li>{fr.prejoin.transcription}</li> : null}
            {meeting.waiting_room && participant.role === "participant" ? (
              <li>{fr.prejoin.waitingRoom}</li>
            ) : null}
          </ul>

          {error ? (
            <p className="note warn" role="alert">
              {error}
            </p>
          ) : null}

          <button type="submit" className="button primary large" disabled={busy}>
            {busy ? fr.prejoin.joining : fr.prejoin.join}
          </button>
          <p className="muted small">{fr.prejoin.role[participant.role]}</p>
        </form>
      </section>
    </main>
  );
}
