import { useEffect, useRef } from "react";

import { ApiError, isAdmitted, join, type JoinResult, type Preview } from "../api";
import type { Brand } from "../brand";
import { BrandBar } from "../components/BrandBar";
import { Icon } from "../components/Icon";
import { fr } from "../fr";
import { saveClientId } from "../storage";

interface Props {
  brand: Brand;
  preview: Preview;
  linkToken: string;
  name: string | null;
  clientId: string | null;
  /** L'organisateur a admis la personne : le jeton de salle est là. */
  onAdmitted: (session: JoinResult, clientId: string | null) => void;
  onDenied: () => void;
  /** Réunion terminée, annulée, lien exclu… : on repart de l'aperçu, qui sait quoi afficher. */
  onGone: () => void;
  onCancel: () => void;
}

const POLL_MS = 2000;

/** Salle d'attente : aucun jeton de salle n'existe tant que l'organisateur n'a pas admis.
 *  La page relit sa demande toutes les 2 s (c'est aussi ce qui la garde visible dans la liste de
 *  l'organisateur) et entre toute seule dès l'admission. */
export function Lobby({
  brand,
  preview,
  linkToken,
  name,
  clientId,
  onAdmitted,
  onDenied,
  onGone,
  onCancel,
}: Props) {
  const handlers = useRef({ onAdmitted, onDenied, onGone });
  handlers.current = { onAdmitted, onDenied, onGone };

  useEffect(() => {
    let cancelled = false;
    let currentClientId = clientId;
    const tick = async () => {
      try {
        const response = await join(linkToken, name, currentClientId);
        if (cancelled) return;
        if (response.client_id) {
          currentClientId = response.client_id;
          saveClientId(response.client_id);
        }
        if (isAdmitted(response)) {
          handlers.current.onAdmitted(response, currentClientId);
          return;
        }
        if (response.status === "denied") {
          handlers.current.onDenied();
          return;
        }
      } catch (error) {
        if (cancelled) return;
        // Réseau coupé un instant : on réessaie. Tout refus du serveur met fin à l'attente.
        if (!(error instanceof ApiError) || error.code !== "network") {
          handlers.current.onGone();
          return;
        }
      }
      timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    let timer = window.setTimeout(() => void tick(), 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [linkToken, name, clientId]);

  return (
    <main className="page">
      <BrandBar brand={brand} />
      <section className="page-main" role="status" data-testid="lobby">
        <p className="meeting-name">{preview.meeting.title}</p>
        <span className="lobby-icon" aria-hidden="true">
          <Icon name="hourglass" size={40} />
        </span>
        <h1>{fr.lobby.title}</h1>
        <p className="lead">{fr.lobby.body}</p>
        <button type="button" className="button" onClick={onCancel}>
          {fr.lobby.leave}
        </button>
      </section>
    </main>
  );
}
