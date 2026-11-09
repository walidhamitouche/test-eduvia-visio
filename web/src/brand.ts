// Marque blanche : le serveur injecte le nom et le logo dans la page (balise JSON) ; les couleurs
// passent par des variables CSS (`--brand-*`). Hors serveur (npm run dev), valeurs neutres.

export interface Brand {
  name: string;
  logoUrl: string | null;
}

export function readBrand(): Brand {
  try {
    const node = document.getElementById("brand-config");
    if (node?.textContent) {
      const data = JSON.parse(node.textContent) as { name?: string; logo_url?: string };
      return { name: data.name ?? "Visio", logoUrl: data.logo_url || null };
    }
  } catch {
    // configuration illisible : valeurs neutres
  }
  return { name: "Visio", logoUrl: null };
}
