import type { ReactNode } from "react";

import type { Brand } from "../brand";
import { BrandBar } from "../components/BrandBar";

interface Props {
  brand: Brand;
  title: string;
  body: string;
  /** Nom de la réunion, quand on la connaît. */
  meeting?: string;
  action?: { label: string; onClick: () => void };
  children?: ReactNode;
}

/** Écran de message : lien invalide, réunion terminée, erreur de connexion... */
export function Notice({ brand, title, body, meeting, action, children }: Props) {
  return (
    <main className="page">
      <BrandBar brand={brand} />
      <section className="page-main" role="status">
        {meeting ? <p className="meeting-name">{meeting}</p> : null}
        <h1>{title}</h1>
        <p className="lead">{body}</p>
        {children}
        {action ? (
          <button type="button" className="button primary" onClick={action.onClick}>
            {action.label}
          </button>
        ) : null}
      </section>
    </main>
  );
}
