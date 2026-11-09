import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from visio import __version__
from visio.api import (
    artifacts,
    devtools,
    health,
    join,
    links,
    livekit_webhook,
    meetings,
    room,
    transcripts,
    web,
    webhook_docs,
)
from visio.api.errors import install_error_handlers
from visio.api.observability import RequestContextMiddleware
from visio.config import Settings, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.domain.clock import Clock, SystemClock
from visio.logging_setup import configure_logging
from visio.services import webhooks
from visio.services.egress import EgressGateway, RecorderGateway
from visio.services.rooms import LiveKitGateway, RoomGateway
from visio.services.storage import ObjectStore, Storage
from visio.workers import scheduler


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    *,
    rooms: RoomGateway | None = None,
    recorder: RecorderGateway | None = None,
    sender: webhooks.Sender | None = None,
    storage: ObjectStore | None = None,
    run_workers: bool = True,
) -> FastAPI:
    """`settings`, `clock`, `rooms` (serveur de médias), `recorder` (Egress), `sender` (webhooks),
    `storage` (S3) et `run_workers` (planificateur en tâche de fond) ne servent qu'aux tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Échec immédiat et explicite si la configuration est incomplète ou invalide.
        resolved = settings or get_settings()
        configure_logging(resolved.log_level, resolved.log_format, resolved.app_env)
        engine = create_db_engine(resolved.database_url)
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.clock = clock or SystemClock()
        gateway = rooms or LiveKitGateway(
            resolved.livekit_url,
            resolved.livekit_api_key,
            resolved.livekit_api_secret.get_secret_value(),
        )
        app.state.rooms = gateway
        egress = recorder or EgressGateway(
            resolved.livekit_url,
            resolved.livekit_api_key,
            resolved.livekit_api_secret.get_secret_value(),
            template_url=resolved.recording_template_url,
            segment_seconds=resolved.recording_segment_seconds,
        )
        app.state.recorder = egress
        app.state.webhook_sender = sender or webhooks.http_sender
        # Pas de connexion au S3 ici (le client est créé sans appel réseau) : l'enregistrement
        # (14b : taille du fichier) et les artefacts (étape 15 : téléchargement, suppression)
        # s'en servent.
        app.state.storage = storage or Storage.from_settings(resolved)
        worker: asyncio.Task[None] | None = None
        if run_workers:
            worker = asyncio.create_task(
                scheduler.run_forever(
                    app.state.session_factory,
                    resolved,
                    gateway,
                    egress,
                    app.state.webhook_sender,
                    app.state.clock,
                    app.state.storage,
                    interval=resolved.scheduler_interval_seconds,
                )
            )
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            engine.dispose()  # Windows verrouille le fichier SQLite tant que le moteur est ouvert

    app = FastAPI(
        title="Eduvia Visio",
        version=__version__,
        description=(
            "API de visioconférence auto-hébergée. Les réunions sont créées uniquement par API.\n\n"
            "Authentification : `Authorization: Bearer <clé d'API>` sur toutes les routes `/v1`. "
            "Dates : ISO 8601 avec décalage horaire. Erreurs : "
            '`{"error": {"code": "...", "message": "..."}}`.'
        ),
        lifespan=lifespan,
        webhooks=webhook_docs.router,
    )
    install_error_handlers(app)
    webhook_docs.install(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(health.detail)
    app.include_router(meetings.router)
    app.include_router(artifacts.router)
    app.include_router(artifacts.downloads)
    app.include_router(transcripts.router)
    app.include_router(links.router)
    app.include_router(join.router)
    app.include_router(room.router)
    app.include_router(livekit_webhook.router)
    app.include_router(web.router)
    # Page d'essai LiveKit : répond 404 hors `APP_ENV=development` (la config n'est lue qu'au
    # démarrage, d'où la vérification à chaque appel plutôt qu'au montage).
    app.include_router(devtools.router)
    return app


app = create_app()
