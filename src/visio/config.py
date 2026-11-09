"""Configuration de l'application, validée au démarrage.

Les valeurs arrivent dans l'environnement du processus, injectées par
`dotenvx run -f .env.<env> -- <commande>` (fichiers chiffrés, une seule clé privée par
environnement). Ce module ne lit jamais de fichier `.env` lui-même : il ne verrait
que des valeurs chiffrées.

Règles (besoin §7) : aucun secret en clair dans le dépôt, les images ou les variables
Coolify ; démarrage refusé avec un message clair si la configuration est incomplète.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER = "CHANGE_ME"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MIN_SECRET_LENGTH = 32


class ConfigurationError(RuntimeError):
    """Configuration invalide ou incomplète. Le message ne contient jamais de valeur."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, frozen=True)

    app_env: Literal["development", "test", "production"]

    # Sécurité de l'API et des liens
    api_key: SecretStr
    link_signing_secret: SecretStr

    # Domaine public (marque blanche) : ex. https://visio.eduvia.app
    public_base_url: str

    # Webhooks sortants vers Eduvia
    webhook_url: str
    webhook_secret: SecretStr

    # LiveKit
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: SecretStr

    # Stockage S3 compatible
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_region: str = "eu-west-3"

    # Marque blanche
    brand_name: str
    brand_logo_url: str
    brand_primary_color: str
    brand_secondary_color: str

    # Base de données (SQLite par défaut, voir docs/DECISIONS.md)
    database_url: str = "sqlite:///./data/visio.db"

    # Logs (étape 17a) : niveau, et format `json` (production) ou `text` ; `auto` = json en
    # production, texte ailleurs.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["auto", "json", "text"] = "auto"

    # Période du planificateur (fin de créneau, fermeture de salle, livraison des webhooks).
    scheduler_interval_seconds: float = 1.0

    # Protection des routes publiques /v1/join : requêtes par minute et par IP (0 = désactivé ;
    # valeur large : un établissement entier peut partager une seule IP, et la salle d'attente
    # relit toutes les 2 s) et plafond de participants connectés par réunion (0 = illimité).
    join_rate_limit_per_minute: int = 1500
    max_participants_per_meeting: int = 40

    # Interface de visio construite (`npm --prefix web run build`), servie par le backend.
    # Chemin relatif au dossier de lancement (la racine du dépôt) ou absolu (Docker, étape 19).
    web_dist_dir: str = "web/dist"

    # Mise en page de l'enregistrement (OBLIGATOIRE) : URL de `/recording-template` **telle que le
    # service Egress la joint** (dev Docker : http://host.docker.internal:8000/recording-template).
    # Plus de repli sur la mise en page « speaker » de LiveKit : sans cette URL, pas de démarrage.
    recording_template_url: str

    # Enregistrement en segments (étape 14d) : durée d'un segment en secondes (0 = un seul MP4,
    # comme avant). Avec une valeur > 0, les segments sont fusionnés en un MP4 unique à la fin
    # de la réunion (ffmpeg requis sur le serveur de l'API). Conseillé : 600 (10 minutes).
    recording_segment_seconds: int = 0

    # Transcription (étape 16c) : moteur faster-whisper **local**. `transcription_model` est le
    # dossier d'un modèle déjà présent sur disque, ou un nom (`medium`, `large-v3`) déjà copié
    # dans `transcription_model_dir`. Le worker n'ira jamais le télécharger (aucun accès réseau).
    transcription_model: str = "large-v3"
    transcription_model_dir: str = "data/models/whisper"
    transcription_device: str = "auto"  # auto | cpu | cuda
    transcription_compute_type: str = "int8"  # int8 (CPU) | float16 (GPU)
    transcription_poll_seconds: float = 5.0
    # DIAGNOSTIC uniquement : garde les pistes audio après la transcription pour pouvoir les écouter
    # (`uv run task tracks-get`). Ce sont des données personnelles : laisser à false en production.
    transcription_keep_audio: bool = False
    # Termes techniques et anglicismes à reconnaître (séparés par des virgules) : ex.
    # « Airflow, Python, Docker, DevOps ». Donné au moteur comme contexte.
    transcription_vocabulary: str = ""
    # Langues parlées dans les réunions, la principale d'abord. Avec plusieurs langues, la langue
    # est choisie PAR PHRASE (une phrase anglaise n'est plus supprimée ni traduite en français).
    # Une seule langue (ex. « fr ») : langue forcée, comme avant. Aucun terme n'est donné au moteur.
    transcription_languages: str = "fr,en"
    # Transcrire chaque phrase dans chaque langue et garder la plus sûre : deux fois plus de calcul.
    transcription_dual: bool = False

    # --- validations champ par champ ---------------------------------------------

    @field_validator("api_key", "link_signing_secret", "webhook_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MIN_SECRET_LENGTH:
            msg = f"doit contenir au moins {_MIN_SECRET_LENGTH} caractères"
            raise ValueError(msg)
        return value

    @field_validator("brand_primary_color", "brand_secondary_color")
    @classmethod
    def _color_is_hex(cls, value: str) -> str:
        if not _HEX_COLOR.match(value):
            msg = "doit être une couleur hexadécimale du type #1A2B3C"
            raise ValueError(msg)
        return value

    @field_validator(
        "public_base_url", "webhook_url", "s3_endpoint", "brand_logo_url", "recording_template_url"
    )
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            msg = "doit commencer par http:// ou https://"
            raise ValueError(msg)
        return value

    @field_validator("livekit_url")
    @classmethod
    def _ws_url(cls, value: str) -> str:
        if not value.startswith(("ws://", "wss://", "http://", "https://")):
            msg = "doit commencer par ws://, wss://, http:// ou https://"
            raise ValueError(msg)
        return value

    @field_validator("brand_name", "s3_bucket", "livekit_api_key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            msg = "ne doit pas être vide"
            raise ValueError(msg)
        return value

    # --- validation globale : durcissement en production ----------------------------

    @model_validator(mode="after")
    def _production_is_strict(self) -> Settings:
        if self.app_env != "production":
            return self

        problems: list[str] = []
        for name in ("public_base_url", "webhook_url", "s3_endpoint", "brand_logo_url"):
            if not getattr(self, name).startswith("https://"):
                problems.append(f"{name.upper()} doit utiliser https:// en production")
        if not self.livekit_url.startswith(("wss://", "https://")):
            problems.append("LIVEKIT_URL doit utiliser wss:// ou https:// en production")

        plain = {
            "BRAND_NAME": self.brand_name,
            "S3_BUCKET": self.s3_bucket,
            "LIVEKIT_API_KEY": self.livekit_api_key,
            "PUBLIC_BASE_URL": self.public_base_url,
            "WEBHOOK_URL": self.webhook_url,
            "LIVEKIT_URL": self.livekit_url,
            "S3_ENDPOINT": self.s3_endpoint,
            "BRAND_LOGO_URL": self.brand_logo_url,
        }
        secrets = {
            "API_KEY": self.api_key,
            "LINK_SIGNING_SECRET": self.link_signing_secret,
            "WEBHOOK_SECRET": self.webhook_secret,
            "LIVEKIT_API_SECRET": self.livekit_api_secret,
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
        }
        for name, value in plain.items():
            if PLACEHOLDER in value:
                problems.append(f"{name} contient encore la valeur provisoire {PLACEHOLDER}")
        for name, secret in secrets.items():
            if PLACEHOLDER in secret.get_secret_value():
                problems.append(f"{name} contient encore la valeur provisoire {PLACEHOLDER}")

        if problems:
            raise ValueError("; ".join(problems))
        return self


def format_validation_error(exc: ValidationError) -> str:
    """Message lisible, sans jamais afficher les valeurs saisies (elles peuvent être secrètes)."""
    lines = ["Configuration invalide :"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]).upper() or "(global)"
        if error["type"] == "missing":
            lines.append(f"  - {location} : variable manquante")
        else:
            message = str(error["msg"]).removeprefix("Value error, ")
            lines.append(f"  - {location} : {message}")
    lines.append(
        "Vérifie le fichier .env de l'environnement "
        "(dotenvx run -f .env.<env> -- ...) et la clé DOTENV_PRIVATE_KEY_<ENV>."
    )
    return "\n".join(lines)


def _undecrypted_variables() -> list[str]:
    """Variables encore chiffrées : dotenvx n'a pas pu les déchiffrer (clé absente ou fausse)."""
    return sorted(
        name.upper()
        for name in Settings.model_fields
        if os.environ.get(name.upper(), "").startswith("encrypted:")
    )


@lru_cache
def get_settings() -> Settings:
    undecrypted = _undecrypted_variables()
    if undecrypted:
        raise ConfigurationError(
            "Configuration chiffrée non déchiffrée : la clé DOTENV_PRIVATE_KEY_<ENV> "
            "est absente ou incorrecte.\n  Variables concernées : " + ", ".join(undecrypted)
        )
    try:
        return Settings()  # type: ignore[call-arg]  # valeurs lues dans l'environnement
    except ValidationError as exc:
        raise ConfigurationError(format_validation_error(exc)) from None
