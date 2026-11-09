// Mémoire locale du navigateur : jamais indispensable (mode privé, stockage bloqué), donc chaque
// accès est protégé.

const CLIENT_ID = "visio.client_id";
const NAME = "visio.display_name";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // tant pis : l'identité sera simplement redemandée au serveur
  }
}

/** Identifiant propre à ce navigateur, fourni par le serveur (lien générique). */
export const getClientId = (): string | null => read(CLIENT_ID);
export const saveClientId = (value: string): void => write(CLIENT_ID, value);
export const getSavedName = (): string => read(NAME) ?? "";
export const saveName = (value: string): void => write(NAME, value);
