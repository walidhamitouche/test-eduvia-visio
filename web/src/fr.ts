// Tous les textes de l'interface, en français. Aucune mention de la brique technique : la marque
// vient de la configuration (nom, logo, couleurs). Pour changer un texte, c'est ici.

export const fr = {
  loading: "Ouverture du lien…",

  // Écrans d'erreur
  invalidLink: {
    title: "Lien invalide",
    body: "Ce lien n'ouvre aucune réunion. Vérifiez qu'il est copié en entier, ou demandez-en un nouveau à l'organisateur.",
  },
  excluded: {
    title: "Accès retiré",
    body: "Vous ne pouvez plus rejoindre cette réunion avec ce lien.",
  },
  removed: {
    title: "Vous avez été retiré de la réunion",
    body: "L'organisateur a mis fin à votre participation. Vous ne pouvez plus revenir avec ce lien.",
  },
  duplicate: {
    title: "Connecté ailleurs",
    body: "Ce lien est utilisé dans un autre onglet ou sur un autre appareil : vous avez été déconnecté de celui-ci.",
    back: "Revenir ici",
  },
  failed: {
    title: "Reconnexion impossible",
    body: "La connexion à la réunion a été perdue et n'a pas pu être rétablie. Vérifiez votre connexion internet, puis revenez dans la réunion.",
    back: "Revenir dans la réunion",
  },
  network: {
    title: "Connexion impossible",
    body: "Le serveur ne répond pas. Vérifiez votre connexion internet, puis réessayez.",
  },
  unknown: {
    title: "Une erreur est survenue",
    body: "La réunion n'a pas pu s'ouvrir. Réessayez dans un instant.",
  },
  retry: "Réessayer",

  // Attente
  waiting: {
    opensAt: "La réunion ouvre à",
    parisTime: "heure de Paris",
    startsAt: "Début de la réunion à {time}.",
    countdown: "Ouverture dans {duration}",
    opening: "Ouverture…",
    keepOpen: "Gardez cette page ouverte : vous pourrez entrer dès l'ouverture.",
  },

  // Réunion terminée ou annulée
  ended: {
    title: "Réunion terminée",
    body: "Cette réunion s'est terminée à {time}. Il n'est plus possible de la rejoindre.",
  },
  cancelled: {
    title: "Réunion annulée",
    body: "Cette réunion a été annulée. Contactez l'organisateur pour connaître la suite.",
  },
  left: {
    title: "Vous avez quitté la réunion",
    body: "Vous pouvez y revenir tant qu'elle est ouverte.",
    back: "Revenir dans la réunion",
  },

  // Pré-jointure
  prejoin: {
    title: "Prêt à entrer ?",
    name: "Votre nom",
    namePlaceholder: "Prénom Nom",
    nameHelp: "Ce nom sera visible des autres participants.",
    nameLocked: "Ce nom est celui de votre invitation. Il ne peut pas être modifié.",
    nameRequired: "Saisissez votre nom pour entrer.",
    microphone: "Micro",
    camera: "Caméra",
    micOn: "Micro activé",
    micOff: "Micro coupé",
    camOn: "Caméra activée",
    camOff: "Caméra coupée",
    defaultDevice: "Appareil par défaut",
    level: "Niveau du micro",
    cameraOff: "Caméra coupée",
    devicesDenied:
      "Le micro ou la caméra est inaccessible. Autorisez-les dans votre navigateur pour être vu et entendu, ou entrez sans.",
    devicesUnavailable: "Votre navigateur ne permet pas d'utiliser le micro ou la caméra sur cette page.",
    join: "Rejoindre la réunion",
    joining: "Connexion…",
    recording: "Cette réunion sera enregistrée (audio, vidéo et partage d'écran).",
    transcription: "Une transcription écrite de la réunion sera produite.",
    // Texte prêt pour l'étape 9 (salle d'attente) : affiché seulement quand elle sera appliquée.
    waitingRoom: "L'organisateur devra vous admettre avant que vous puissiez entrer.",
    role: {
      organizer: "Vous entrez en tant qu'organisateur.",
      participant: "Vous entrez en tant que participant.",
    },
  },

  // Salle d'attente (participant)
  lobby: {
    title: "En attente d'admission",
    body: "L'organisateur va vous admettre dans la réunion. Gardez cette page ouverte : vous entrerez automatiquement.",
    meeting: "Réunion",
    leave: "Annuler",
  },
  denied: {
    title: "Accès refusé",
    body: "L'organisateur n'a pas accepté votre entrée dans cette réunion.",
  },

  // Salle
  room: {
    endsAt: "Fin à {time}",
    recording: "Enregistrement en cours",
    connecting: "Connexion à la réunion…",
    reconnecting: "Connexion perdue, reconnexion…",
    reconnectFailed: "Impossible de revenir dans la réunion.",
    you: "vous",
    organizer: "Organisateur",
    micMute: "Couper le micro",
    micUnmute: "Activer le micro",
    camMute: "Couper la caméra",
    camUnmute: "Activer la caméra",
    shareStart: "Partager l'écran",
    shareStop: "Arrêter le partage",
    chat: "Discussion",
    participants: "Participants",
    leave: "Quitter",
    close: "Fermer",
    sharing: "Écran partagé par {name}",
    endWarning: "La réunion se termine dans {duration}.",
    endWarningNow: "La réunion va se terminer dans {duration}.",
    endMeeting: "Terminer la réunion",
    endConfirmTitle: "Terminer la réunion pour tout le monde ?",
    endConfirmBody: "Tous les participants seront déconnectés et personne ne pourra revenir.",
    endConfirm: "Terminer pour tous",
    cancel: "Annuler",
    waitingRoom: "Salle d'attente",
    moderationError: "L'action n'a pas pu être effectuée. Réessayez.",
    cameraOff: "Caméra coupée",
    micMuted: "Micro coupé",
    enableSound: "Activer le son de la réunion",
    mediaError: "Impossible d'utiliser {device}. Vérifiez les autorisations de votre navigateur.",
    shareError: "Le partage d'écran n'a pas pu démarrer.",
    devices: { microphone: "le micro", camera: "la caméra" },
  },

  chat: {
    title: "Discussion",
    empty: "Aucun message pour l'instant. Écrivez le premier.",
    placeholder: "Écrire un message",
    send: "Envoyer",
    sendError: "Le message n'a pas pu être envoyé.",
    unread: "{count} nouveau(x) message(s)",
  },

  people: {
    title: "Participants ({count})",
    micMuted: "Micro coupé",
    muteMic: "Couper le micro de {name}",
    muteCamera: "Couper la caméra de {name}",
    stopShare: "Arrêter le partage de {name}",
    remove: "Exclure {name}",
    removeConfirm: "Exclure {name} définitivement ? Cette personne ne pourra plus revenir.",
    removeYes: "Exclure",
    removeNo: "Annuler",
    micShort: "Micro",
    cameraShort: "Caméra",
    shareShort: "Partage",
    removeShort: "Exclure",
  },

  lobbyPanel: {
    title: "Salle d'attente ({count})",
    empty: "Personne n'attend.",
    admit: "Admettre {name}",
    deny: "Refuser {name}",
    admitShort: "Admettre",
    denyShort: "Refuser",
    admitAll: "Tout admettre",
    denied: "Refusé",
    waitingNow: "{count} personne(s) en attente d'admission.",
  },
} as const;

export type Strings = typeof fr;

/** Remplace `{nom}` par la valeur donnée : t("Fin à {time}", { time: "18:00" }). */
export function t(template: string, values: Record<string, string | number> = {}): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );
}
