"""Stockage S3 compatible (étape 13b) : enregistrements, transcriptions (étapes 14 à 16).

Un seul point d'accès au S3 pour tout le code : endpoint, bucket et clés viennent de la
configuration (`S3_*`), donc MinIO en développement et le S3 d'Eduvia en production sont
atteints par le même code.

Le bucket est **privé** : rien n'est jamais servi directement par le S3. Les artefacts sortent
par un proxy de l'API (étape 15), pour qu'une URL déjà émise cesse de marcher dès la suppression.

Le client `boto3` est synchrone : depuis du code asynchrone, l'appeler avec
`asyncio.to_thread(...)`.
"""

from __future__ import annotations

import contextlib
import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from visio.config import Settings

logger = logging.getLogger("visio.storage")

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}
CHUNK_SIZE = 1024 * 1024  # taille des morceaux d'un téléchargement servi par l'API


class StorageError(RuntimeError):
    """Le S3 est injoignable ou a refusé l'opération. Le message ne contient jamais de clé."""


class ObjectNotFoundError(StorageError):
    """L'objet demandé n'existe pas dans le bucket."""


def _code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


class StoredRange:
    """Un morceau d'objet ouvert en lecture. `chunks()` le lit en flux, puis le referme : un
    enregistrement de plusieurs centaines de Mo ne passe jamais en entier par la mémoire."""

    def __init__(self, body: Any, length: int) -> None:
        self._body = body
        self.length = length  # octets que ce flux doit fournir

    def chunks(self, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        try:
            while True:
                data: bytes = self._body.read(chunk_size)
                if not data:
                    return
                yield data
        finally:
            self.close()

    def close(self) -> None:
        with contextlib.suppress(Exception):  # fermer ne doit jamais masquer une autre erreur
            self._body.close()


class Storage:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str,
    ) -> None:
        self.bucket = bucket
        self.endpoint = endpoint
        self.region = region
        # Adressage « path » (http://hote/bucket/objet) : le seul que MinIO accepte sur
        # localhost, et accepté partout ailleurs. Peu de tentatives : un S3 en panne doit se
        # voir vite plutôt que de bloquer une requête.
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )
        # Client de la sonde de santé (17b) : aucune nouvelle tentative et des délais courts, pour
        # qu'un S3 coupé réponde « injoignable » au lieu d'épuiser le délai de la sonde.
        self._probe_client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=2,
                read_timeout=2,
            ),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Storage:
        return cls(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key.get_secret_value(),
            secret_key=settings.s3_secret_key.get_secret_value(),
            region=settings.s3_region,
        )

    # --- bucket ---------------------------------------------------------------------------

    def bucket_exists(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            if _code(error) in _NOT_FOUND_CODES:
                return False
            raise StorageError(f"bucket inaccessible ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return True

    def ensure_bucket(self) -> bool:
        """Crée le bucket s'il manque. Renvoie True s'il vient d'être créé.

        Pour le développement (MinIO). En production le bucket est fourni par Eduvia : il n'est
        pas créé par l'application (voir `tools/s3check.py`, qui refuse de le faire).
        """
        if self.bucket_exists():
            return False
        try:
            if self.region == "us-east-1":
                self._client.create_bucket(Bucket=self.bucket)
            else:  # les S3 de type AWS exigent la région (MinIO l'ignore)
                self._client.create_bucket(
                    Bucket=self.bucket,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
        except ClientError as error:
            raise StorageError(f"création du bucket refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return True

    def ping(self) -> None:
        """Sonde de santé (étape 17b) : le S3 répond et le bucket existe, sinon `StorageError`.

        `head_bucket` ne lit ni n'écrit aucun objet. Client dédié (voir `__init__`)."""
        try:
            self._probe_client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            if _code(error) in _NOT_FOUND_CODES:
                msg = "bucket introuvable"
                raise StorageError(msg) from error
            raise StorageError(f"bucket inaccessible ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error

    # --- objets ---------------------------------------------------------------------------

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        _check_key(key)
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
        except ClientError as error:
            raise StorageError(f"écriture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error

    def get(self, key: str) -> bytes:
        _check_key(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            body: bytes = response["Body"].read()
        except ClientError as error:
            if _code(error) in _NOT_FOUND_CODES:
                raise ObjectNotFoundError("objet introuvable") from error
            raise StorageError(f"lecture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return body

    def exists(self, key: str) -> bool:
        _check_key(key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _code(error) in _NOT_FOUND_CODES:
                return False
            raise StorageError(f"lecture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return True

    def size(self, key: str) -> int | None:
        """Taille en octets de l'objet (`head_object`), ou `None` s'il n'existe pas."""
        _check_key(key)
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _code(error) in _NOT_FOUND_CODES:
                return None
            raise StorageError(f"lecture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return int(head["ContentLength"])

    def copy(self, source: str, destination: str) -> None:
        """Copie un objet côté S3 (sans le télécharger) : sert aux simulations."""
        _check_key(source)
        _check_key(destination)
        try:
            self._client.copy_object(
                Bucket=self.bucket,
                Key=destination,
                CopySource={"Bucket": self.bucket, "Key": source},
            )
        except ClientError as error:
            raise StorageError(f"copie refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error

    def download_to(self, key: str, path: Path) -> None:
        """Télécharge un objet vers un fichier (sans le charger en mémoire)."""
        _check_key(key)
        try:
            self._client.download_file(self.bucket, key, str(path))
        except ClientError as error:
            raise StorageError(f"lecture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error

    def upload_from(
        self, key: str, path: Path, *, content_type: str = "application/octet-stream"
    ) -> None:
        """Envoie un fichier (transfert fractionné automatique pour les gros fichiers)."""
        _check_key(key)
        try:
            self._client.upload_file(
                str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
            )
        except ClientError as error:
            raise StorageError(f"écriture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error

    def list_objects(self, prefix: str = "") -> list[tuple[str, int]]:
        """Clés (et tailles en octets) des objets dont le chemin commence par `prefix`."""
        found: list[tuple[str, int]] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                found.extend((str(o["Key"]), int(o["Size"])) for o in page.get("Contents", []))
        except ClientError as error:
            raise StorageError(f"liste refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return found

    def delete(self, key: str) -> None:
        """Supprime l'objet. Sans erreur s'il n'existe déjà plus (le S3 répond 204 dans les deux
        cas) : c'est à l'appelant de savoir, par sa base, si l'artefact existait."""
        _check_key(key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            raise StorageError(f"suppression refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error

    def open_range(self, key: str, start: int, end: int) -> StoredRange:
        """Ouvre en flux les octets `start` à `end` (inclus) de l'objet (téléchargements, étape 15).

        La lecture reste du ressort de l'appelant : `StoredRange.chunks()` la fait et referme.
        """
        _check_key(key)
        if start < 0 or end < start:
            msg = "plage d'octets invalide"
            raise ValueError(msg)
        try:
            response = self._client.get_object(
                Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}"
            )
        except ClientError as error:
            if _code(error) in _NOT_FOUND_CODES:
                raise ObjectNotFoundError("objet introuvable") from error
            raise StorageError(f"lecture refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return StoredRange(response["Body"], int(response["ContentLength"]))

    def purge(self, prefix: str) -> int:
        """Supprime **tout** objet dont la clé commence par `prefix` et renvoie leur nombre.

        Sert à la suppression d'un artefact (étape 15) : le fichier, mais aussi ce qu'Egress a pu
        déposer à côté (manifeste `.json`, segments `.ts` non fusionnés). Si le bucket est
        **versionné**, toutes les versions et les marqueurs de suppression partent aussi : sans
        cela, « supprimer » ne ferait que masquer l'objet et il resterait récupérable.
        """
        _check_key(prefix)
        removed = 0
        try:
            if self._versioning_enabled():
                paginator = self._client.get_paginator("list_object_versions")
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    for entry in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
                        self._client.delete_object(
                            Bucket=self.bucket, Key=entry["Key"], VersionId=entry["VersionId"]
                        )
                        removed += 1
            else:
                for name, _size in self.list_objects(prefix):
                    self._client.delete_object(Bucket=self.bucket, Key=name)
                    removed += 1
        except ClientError as error:
            raise StorageError(f"suppression refusée ({_code(error)})") from error
        except BotoCoreError as error:
            raise StorageError(f"S3 injoignable : {type(error).__name__}") from error
        return removed

    def _versioning_enabled(self) -> bool:
        """Vrai si le bucket garde les anciennes versions (`Enabled` ou `Suspended`).

        Une clé d'accès restreinte peut ne pas avoir le droit de le lire, et certains S3 ne
        connaissent pas la question : dans ces cas on suppose « non versionné » (suppression
        simple), ce que l'on consigne pour que l'exploitation le sache.
        """
        try:
            status = self._client.get_bucket_versioning(Bucket=self.bucket).get("Status")
        except ClientError as error:
            logger.warning("versionnement du bucket illisible (%s) : supposé inactif", _code(error))
            return False
        return bool(status)


class ObjectSizer(Protocol):
    """Ce que l'enregistrement demande au S3 : la taille d'un objet (`None` s'il est absent)."""

    def size(self, key: str) -> int | None: ...


class ObjectStore(ObjectSizer, Protocol):
    """Ce que les artefacts demandent au S3 (étape 15) : lire une plage, supprimer par préfixe."""

    def ping(self) -> None: ...

    def purge(self, prefix: str) -> int: ...

    def open_range(self, key: str, start: int, end: int) -> StoredRange: ...

    def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None: ...

    def get(self, key: str) -> bytes: ...


@dataclass
class FakeStorage:
    """Faux S3 en mémoire pour les tests : des objets (`objects`, avec leur contenu) ou de simples
    tailles (`sizes`, pour l'enregistrement qui ne lit jamais le contenu), ou une panne simulée.
    """

    sizes: dict[str, int] = field(default_factory=dict)
    objects: dict[str, bytes] = field(default_factory=dict)
    unreachable: bool = False

    def _check_reachable(self) -> None:
        if self.unreachable:
            msg = "S3 injoignable (simulé)"
            raise StorageError(msg)

    def ping(self) -> None:
        self._check_reachable()

    def size(self, key: str) -> int | None:
        self._check_reachable()
        if key in self.objects:
            return len(self.objects[key])
        return self.sizes.get(key)

    def purge(self, prefix: str) -> int:
        self._check_reachable()
        doomed = [key for key in {*self.objects, *self.sizes} if key.startswith(prefix)]
        for key in doomed:
            self.objects.pop(key, None)
            self.sizes.pop(key, None)
        return len(doomed)

    def open_range(self, key: str, start: int, end: int) -> StoredRange:
        self._check_reachable()
        if key not in self.objects:
            msg = "objet introuvable"
            raise ObjectNotFoundError(msg)
        part = self.objects[key][start : end + 1]
        return StoredRange(io.BytesIO(part), len(part))

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        self._check_reachable()
        _check_key(key)
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        self._check_reachable()
        if key not in self.objects:
            msg = "objet introuvable"
            raise ObjectNotFoundError(msg)
        return self.objects[key]


def _check_key(key: str) -> None:
    """Refuse les clés vides, absolues ou qui remontent l'arborescence : les chemins sont
    toujours construits par l'application (`meetings/<id>/recordings/<uuid>.mp4`)."""
    if not key or key.startswith("/") or ".." in key.split("/"):
        msg = "clé d'objet invalide"
        raise ValueError(msg)
