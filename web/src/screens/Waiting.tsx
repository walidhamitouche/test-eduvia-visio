import { useEffect, useState } from "react";

import type { Preview } from "../api";
import type { Brand } from "../brand";
import { BrandBar } from "../components/BrandBar";
import { fr, t } from "../fr";
import { formatDuration, parisDate, parisTime } from "../time";

interface Props {
  brand: Brand;
  preview: Preview;
  /** Heure du serveur moins heure de l'appareil, en ms : le compte à rebours suit le serveur. */
  clockOffset: number;
  /** À appeler quand l'heure d'ouverture est atteinte (ou à intervalle, pour suivre un report). */
  onRecheck: () => void;
}

const RECHECK_EVERY_MS = 30_000;

/** Lien valide, réunion pas encore ouverte : l'heure d'ouverture, en heure de Paris. */
export function Waiting({ brand, preview, clockOffset, onRecheck }: Props) {
  const opensAt = new Date(preview.opens_at).getTime();
  const [now, setNow] = useState(() => Date.now() + clockOffset);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() + clockOffset), 1000);
    return () => window.clearInterval(timer);
  }, [clockOffset]);

  const remaining = (opensAt - now) / 1000;
  const reached = remaining <= 0;

  useEffect(() => {
    // Avant l'heure : on relit l'aperçu de temps en temps, pour suivre un report du créneau.
    // Une fois l'heure atteinte : toutes les 2 s, jusqu'à ce que le serveur dise « ouvert ».
    if (reached) onRecheck();
    const timer = window.setInterval(onRecheck, reached ? 2000 : RECHECK_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [reached, onRecheck]);

  const early = preview.opens_at !== preview.meeting.starts_at;

  return (
    <main className="page">
      <BrandBar brand={brand} />
      <section className="page-main">
        <p className="meeting-name">{preview.meeting.title}</p>
        <h1 className="sr-only">{fr.waiting.opensAt}</h1>
        <p className="lead-in">{fr.waiting.opensAt}</p>
        <p className="big-time" data-testid="opens-at">
          <time dateTime={preview.opens_at}>{parisTime(preview.opens_at)}</time>
        </p>
        <p className="lead" data-testid="opens-date">
          {parisDate(preview.opens_at)}, {fr.waiting.parisTime}
        </p>
        {early ? (
          <p className="muted">{t(fr.waiting.startsAt, { time: parisTime(preview.meeting.starts_at) })}</p>
        ) : null}
        <p className="countdown" aria-live="off" data-testid="countdown">
          {reached
            ? fr.waiting.opening
            : t(fr.waiting.countdown, { duration: formatDuration(remaining) })}
        </p>
        <p className="muted">{fr.waiting.keepOpen}</p>
      </section>
    </main>
  );
}
