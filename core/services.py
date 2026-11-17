"""Agrupa servicios del asistente sin acoplar el bus a Django ni a Windows."""

from __future__ import annotations

import os
import asyncio
from dataclasses import asdict
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.system.actions import ActionController
from core.system.alerts import AlertEngine
from core.memory.audit import AuditStore
from core.events import EventType, ThornEvent
from core.voice.elevenlabs_voice import ElevenLabsVoice, VoiceConfig
from core.brain.local_llm import LocalLanguageModel
from core.voice.speech_input import LocalSpeechInput
from core.network.internet import InternetService
from core.network.network_sensor import NetworkSensor
from core.voice.local_voice import LocalVoice
from core.brain.operator import OperatorServices
from core.version import MODEL_PROFILE
from core.memory.conversation_memory import ConversationMemory

if TYPE_CHECKING:
    from core.runtime import ThornRuntime


class AssistantServices:
    """IA y voz locales por defecto; nunca abre el microfono desde el servidor."""

    def __init__(self, runtime: ThornRuntime, *, database: bool, sensor_interval: float) -> None:
        self.runtime = runtime
        self.audit = AuditStore(enabled=database)
        self.conversations = ConversationMemory(self.audit, enabled=os.getenv("THORN_CONVERSATION_MEMORY", "1") != "0")
        self.alerts = AlertEngine(runtime.bus, self.audit, max_gap=max(5, sensor_interval * 3))
        self.actions = ActionController(self.audit)
        self.language = LocalLanguageModel(
            self.audit, endpoint=os.getenv("THORN_LLM_URL", "http://127.0.0.1:11434"),
            model=os.getenv("THORN_LLM_MODEL", MODEL_PROFILE),
        )
        self.local_ai_only = os.getenv("THORN_LOCAL_AI_ONLY", "1") != "0"
        provider = os.getenv("THORN_VOICE_PROVIDER", "piper")
        # NOTA DEL OPERADOR: el modo independiente decide aqui que proveedor
        # puede arrancar. Tener una API key guardada no habilita la nube.
        if provider == "piper":
            self.voice = LocalVoice(self.audit)
        elif provider == "elevenlabs" and not self.local_ai_only:
            self.voice = ElevenLabsVoice(self.audit, VoiceConfig.from_env())
        else:
            self.voice = LocalVoice(self.audit, enabled=False)
            self.voice.state = "policy_blocked" if provider == "elevenlabs" else "invalid_configuration"
        self.microphone = LocalSpeechInput(self.audit)
        self.conversation_auto_start = os.getenv("THORN_CONVERSATION_AUTO_START", "0") == "1"
        # La red local avisa antes de esperar a que el DNS falle por tiempo.
        self.internet = InternetService(self.audit, runtime.bus, offline=lambda: self.network.offline())
        self.network = NetworkSensor(runtime.bus)
        self.operator = OperatorServices(self)
        runtime.sessions.on_release(self.operator.store.release)
        for topic in (EventType.RUNTIME_STARTED, EventType.RUNTIME_STOPPING):
            runtime.bus.subscribe(topic, self._record_lifecycle)

    async def start(self) -> None:
        await self.audit.initialize()
        await self.conversations.initialize()
        await self.operator.start()
        startup = [self.microphone.start(), self.language.start()]
        if isinstance(self.voice, LocalVoice):
            startup.append(self.voice.start())
        await asyncio.gather(*startup)
        await self.network.start()
        saved = await self.audit.load_setting("alerts")
        if saved is not None:
            await self.alerts.configure(saved, persist=False)

    async def _record_lifecycle(self, event: ThornEvent) -> None:
        await self.audit.append(event.event_type, "observed", "Ciclo de vida del nucleo")

    def context(self) -> dict[str, object]:
        # T.H.O.R.N necesita lecturas vigentes para hablar del estado del equipo.
        # Si la muestra vencio, mandamos None; completar el dato seria inventarlo.
        sensor = self.runtime.system_sensor
        fresh = sensor is not None and sensor.stats["fresh"]
        network = self.network.snapshot if self.network.status["fresh"] else None
        return {"server_time": datetime.now(timezone.utc).isoformat(),
                "telemetry_fresh": fresh,
                "telemetry": asdict(sensor.snapshot) if fresh and sensor.snapshot else None,
                "active_alerts": list(self.alerts.active.values()),
                "dictation_available": self.microphone.status["available"],
                "uptime_seconds": self.runtime.uptime_seconds,
                "actions_available": self.actions.enabled,
                "voice": {"provider": self.voice.status["provider"], "cloud": self.voice.status["cloud"],
                          "available": self.voice.status["available"]},
                "network_local": {"fresh": network is not None,
                    "gateway_ipv4": network["gateway_ipv4"] if network else None,
                    "upload_bps": network["upload_bps"] if network else None,
                    "download_bps": network["download_bps"] if network else None}}

    async def web_answer(self, result: dict[str, object], question: str) -> dict[str, object]:
        """Resume sin notas, telemetria, secretos ni herramientas OS en el contexto."""
        sources = result["sources"]
        reply = "No se encontraron fuentes para esa consulta."
        method = "extractive"
        if sources:
            reply = "Extractos de las fuentes consultadas:\n" + "\n".join(
                f"[{index}] {source['title'][:80]}: {source['excerpt'][:430]}"
                for index, source in enumerate(sources, 1))
            if (await self.language.probe())["available"]:
                try:
                    # NOTA DEL OPERADOR: una pagina web es una fuente, no una
                    # orden. Su resumen recibe extractos y carece de acciones OS.
                    answer = await self.language.ask(question, query_id=result["request_id"], context={
                        "web_sources_untrusted": [{"number": index, "title": source["title"][:100],
                            "excerpt": source["excerpt"][:1500 if len(sources) == 1 else 430],
                            "retrieved_at": source.get("retrieved_at"), "published_at_declared": source.get("published_at")}
                            for index, source in enumerate(sources, 1)],
                        "retrieved_at": result["retrieved_at"], "actions_available": False})
                    reply, method = answer["reply"][:1800], "local_llm"
                except RuntimeError:
                    # La lectura sigue siendo util sin Ollama o si el resumen falla.
                    pass
        return {**result, "reply": reply, "summary_method": method,
                "suggested_action": None, "executed": False, "source": "internet"}

    async def stop(self) -> None:
        """Intenta cerrar todos los servicios incluso si uno falla al liberar recursos."""
        async with AsyncExitStack() as cleanup:
            for service in (self.microphone, self.voice, self.language, self.actions, self.internet, self.network, self.operator):
                cleanup.push_async_callback(service.stop)
