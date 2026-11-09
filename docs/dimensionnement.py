"""Calcul du dimensionnement Eduvia Visio (pire cas 15 x 40, tout activé).

Usage : python dimensionnement.py
Bibliothèque standard uniquement. Chaque constante est soit MESURÉE (DECISIONS.md §6 quindecies,
points cités), soit une HYPOTHÈSE (marquée H) à confirmer sur le serveur cible.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Calibrage sur les mesures du projet -------------------------------------------------------
# Essai « 2 salles de 26 » (points 223, 230, 249) : LiveKit ≈ 2,3 coeurs pour 52 connexions,
# soit 1,15 coeur par salle ; 4 caméras + 6 micros par salle ; sortie 28 à 57 Mbit/s par salle.
CALIB_ROOM_CORES = 1.15
CALIB_PAX, CALIB_CAMS, CALIB_MICS = 26, 4, 6

# Débits et paquets par flux (H = hypothèse, valeurs usuelles WebRTC/Opus simulcast)
AUDIO_MBPS, AUDIO_PPS = 0.04, 50  # Opus ~32 kbit/s + en-têtes (H)
H360_MBPS, H360_PPS = 0.45, 50  # couche 360p (H)
H180_MBPS, H180_PPS = 0.16, 30  # couche 180p, tuiles d'une grille de 40 (H)
SCREEN_MBPS, SCREEN_PPS = 1.7, 150  # partage d'écran 720p (H)
PUB_CAM_MBPS, PUB_CAM_PPS = 2.31, 230  # caméra 720p en simulcast : 3 couches (H)


def _calib_pps_per_core() -> float:
    subs_video = CALIB_CAMS * (CALIB_PAX - 1)
    subs_audio = CALIB_MICS * (CALIB_PAX - 1)
    ingress = CALIB_CAMS * PUB_CAM_PPS + CALIB_MICS * AUDIO_PPS
    pps = subs_video * H360_PPS + subs_audio * AUDIO_PPS + ingress
    return pps / CALIB_ROOM_CORES


PPS_PER_CORE = _calib_pps_per_core()  # ≈ 11 900 paquets/s par coeur (Docker Desktop : pessimiste)


@dataclass
class Scenario:
    name: str
    rooms: int
    pax: int
    cams: int  # caméras allumées par salle
    mics: int  # micros ouverts par salle
    audio_active: float  # part des micros qui émettent réellement (DTX coupe le silence)
    layer: str  # "h360" ou "h180" : couche reçue par tuile
    screen_share: int  # partages d'écran par salle
    recorded: int  # salles enregistrées
    hours: float = 8.0


def room_load(s: Scenario) -> dict[str, float]:
    vid_mbps, vid_pps = (H360_MBPS, H360_PPS) if s.layer == "h360" else (H180_MBPS, H180_PPS)
    others = s.pax - 1
    v_subs = s.cams * others
    a_subs = s.mics * s.audio_active * others
    sc_subs = s.screen_share * others
    pps_out = v_subs * vid_pps + a_subs * AUDIO_PPS + sc_subs * SCREEN_PPS
    mbps_out = v_subs * vid_mbps + a_subs * AUDIO_MBPS + sc_subs * SCREEN_MBPS
    pps_in = (
        s.cams * PUB_CAM_PPS + s.mics * s.audio_active * AUDIO_PPS + s.screen_share * SCREEN_PPS
    )
    mbps_in = (
        s.cams * PUB_CAM_MBPS + s.mics * s.audio_active * AUDIO_MBPS + s.screen_share * SCREEN_MBPS
    )
    if s.recorded:  # l'enregistreur est un abonné de plus : 4 vidéos 360p + les voix actives
        pps_out += 4 * H360_PPS + s.mics * s.audio_active * AUDIO_PPS
        mbps_out += 4 * H360_MBPS + s.mics * s.audio_active * AUDIO_MBPS
    return {
        "cores": (pps_out + pps_in) / PPS_PER_CORE,
        "out_mbps": mbps_out,
        "in_mbps": mbps_in,
        "video_share_out": v_subs * vid_mbps / mbps_out if mbps_out else 0.0,
    }


MARGIN = 1.5  # marge du plan (point 226)
EGRESS_COMPOSITE_CORES, EGRESS_COMPOSITE_GIB = (
    4,
    4,
)  # recommandation LiveKit, mesuré 1,6 moy / 3 pic
EGRESS_TRACK_CORES, EGRESS_TRACK_GIB = 0.05, 0.06  # H : piste Opus -> OGG, à MESURER
S3_UPLOAD_MBPS_PER_REC, TRACK_MBPS = 3.2, 0.064  # MP4 720p30 + piste 64 kbit/s (point 227)
MP4_GB_PER_H = 1.5  # 3,1 Mbit/s = 1,4 Go/h, arrondi (point 227 : 1 à 1,5)
TRACK_MB_PER_H = 29  # point 227
CPU_RTF_ROUTED = 2.0  # mesuré, point 240 (PC portable)
GPU_REALTIME_FACTOR = 5.0  # H : ~5x temps réel avec routage fr,en
PHYS_SPEECH_PER_MEETING_H = 1.5  # H : somme des paroles = 1,5 x durée (chevauchements)


def report(s: Scenario) -> dict[str, float]:
    r = room_load(s)
    cores = r["cores"] * s.rooms
    out = r["out_mbps"] * s.rooms
    inn = r["in_mbps"] * s.rooms
    tracks = s.rooms * s.mics
    eg_cpu = s.recorded * EGRESS_COMPOSITE_CORES + tracks * EGRESS_TRACK_CORES
    eg_ram = s.recorded * EGRESS_COMPOSITE_GIB + tracks * EGRESS_TRACK_GIB
    s3_mbps = s.recorded * S3_UPLOAD_MBPS_PER_REC + tracks * TRACK_MBPS
    meeting_h = s.rooms * s.hours
    return {
        "connexions": s.rooms * s.pax,
        "livekit_cores": cores,
        "livekit_cores_marge": cores * MARGIN,
        "out_gbps": out / 1000,
        "out_gbps_marge": out * MARGIN / 1000,
        "in_gbps": inn / 1000,
        "composites": s.recorded,
        "track_egress": tracks,
        "egress_cpu": eg_cpu,
        "egress_ram_gib": eg_ram,
        "s3_mbps": s3_mbps,
        "mp4_gb_day": s.recorded * s.hours * MP4_GB_PER_H,
        "tracks_gb_day": tracks * s.hours * TRACK_MB_PER_H / 1000,
        "speech_h_day": meeting_h * PHYS_SPEECH_PER_MEETING_H,
        "track_h_day": tracks * s.hours,
        "video_share_out": r["video_share_out"],
    }


SCENARIOS = [
    Scenario(
        "A. Réf. des essais (10 salles de 30, 4 caméras, 3 enreg.)", 10, 30, 4, 6, 1.0, "h360", 0, 3
    ),
    Scenario("B. PIRE CAS RETENU 15 x 40 tout activé", 15, 40, 40, 40, 0.25, "h180", 1, 15),
    Scenario(
        "C. Idem B, 40 voix en même temps (limite physique)", 15, 40, 40, 40, 1.0, "h180", 1, 15
    ),
]


def scale_table(rooms_list=(1, 5, 10, 15)) -> list[dict[str, float]]:
    """Même réunion de 40, tout activé, multipliée : 1 salle, puis 5, 10, 15 en simultané."""
    rows = []
    for n in rooms_list:
        sc = Scenario(f"{n} x 40", n, 40, 40, 40, 0.25, "h180", 1, n)
        rep = report(sc)
        rep["rooms"] = n
        rep["gpu_6h"] = rep["speech_h_day"] / GPU_REALTIME_FACTOR / 6
        rows.append(rep)
    return rows


if __name__ == "__main__":
    print("Par nombre de réunions simultanées (40 participants, tout activé) :")
    for row in scale_table():
        print(
            f"  {row['rooms']:>2.0f} salles : LiveKit {row['livekit_cores_marge']:6.1f} vCPU, "
            f"sortie {row['out_gbps_marge']:4.2f} Gbit/s, Egress {row['egress_cpu']:5.1f} vCPU / "
            f"{row['egress_ram_gib']:5.1f} Gio, MP4 {row['mp4_gb_day']:5.0f} Go/j, "
            f"pistes {row['tracks_gb_day']:5.1f} Go/j, GPU(6h) {row['gpu_6h']:.1f}"
        )
    print()
    print(f"Calibrage : {PPS_PER_CORE:,.0f} paquets/s par coeur\n")
    for sc in SCENARIOS:
        rep = report(sc)
        print(sc.name)
        for k, v in rep.items():
            print(f"  {k:18s} {v:12,.2f}")
        print()
    b = report(SCENARIOS[1])
    print(
        "Transcription GPU-heures pour la pointe (tout fini à la même heure) :",
        round(b["speech_h_day"] / GPU_REALTIME_FACTOR, 1),
    )
    for delay in (3, 4, 6, 12, 24):
        print(f"  délai {delay:>2} h -> {b['speech_h_day'] / GPU_REALTIME_FACTOR / delay:4.1f} GPU")
    print(
        "1 h de réunion : GPU",
        round(PHYS_SPEECH_PER_MEETING_H / GPU_REALTIME_FACTOR * 60),
        "min ;",
        "CPU",
        round(PHYS_SPEECH_PER_MEETING_H * CPU_RTF_ROUTED, 1),
        "h (PC portable)",
    )
