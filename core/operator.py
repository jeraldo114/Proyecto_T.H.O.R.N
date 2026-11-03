"""Coordinacion Bastion 9: turnos, comandos, documentos y resultados con propietario."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from .commands import coordinate, normalize
from .documents import DocumentService
from .interaction import InteractionState, InteractionStore
from .maps import find_places
from .preferences import OperatorPreferences
from .version import RELEASE

if TYPE_CHECKING:
    from .services import AssistantServices


class OperatorServices:
    """La interfaz pide; los servicios entregan datos tipados, nunca HTML ejecutable."""

    def __init__(self, services: AssistantServices) -> None:
        self.services = services
        self.preferences = OperatorPreferences(services.audit)
        self.documents = DocumentService()
        self.store = InteractionStore(on_release=self.documents.release)
        self.sweeper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.preferences.start()
        self.sweeper = asyncio.create_task(self._sweep(), name="thorn.sessions.cleanup")

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(10)
            self.services.runtime.sessions.prune()
            self.store.prune()

    def is_current(self, owner: str, state: InteractionState, generation: int) -> bool:
        task = asyncio.current_task()
        return (self.store.states.get(owner) is state and state.generation == generation
                and (task is None or not task.cancelling()))

    def ensure_current(self, owner: str, state: InteractionState, generation: int) -> None:
        if not self.is_current(owner, state, generation):
            raise RuntimeError("interaction_cancelled")

    async def run(self, owner: str, work: Callable[[], Awaitable[dict[str, object]]]) -> dict[str, object]:
        """Registra tareas por sesion para cancelar tambien operaciones de red/PDF."""
        state = self.store.state(owner, touch=True)
        if owner in self.store.tasks and not self.store.tasks[owner].done():
            raise RuntimeError("interaction_busy")
        generation = state.generation
        state.phase = "processing"
        task = asyncio.create_task(work(), name="thorn.operator.request")
        self.store.tasks[owner] = task
        try:
            result = await task
            if self.store.states.get(owner) is not state or state.generation != generation:
                self.documents.release(owner)
                raise RuntimeError("interaction_cancelled")
            state.phase = "ready"
            return result
        except asyncio.CancelledError:
            if self.store.states.get(owner) is state:
                state.phase = "ready"
            current = asyncio.current_task()
            if current is not None and not current.cancelling():
                raise RuntimeError("interaction_cancelled") from None
            raise
        except Exception:
            if self.store.states.get(owner) is state:
                state.phase = "error"
            raise
        finally:
            if self.store.tasks.get(owner) is task:
                self.store.tasks.pop(owner, None)

    async def briefing(self, owner: str) -> dict[str, object]:
        """Resumen determinista: no consulta un LLM ni hace solicitudes externas."""
        now = datetime.now(timezone.utc).astimezone(self.preferences.zone)
        context = self.services.context()
        name = self.preferences.value["display_name"]
        reply = f"Bienvenido{', ' + name if name else ''}. Son las {now:%H:%M}. {RELEASE} esta listo."
        telemetry = context["telemetry"]
        if context["telemetry_fresh"] and isinstance(telemetry, dict):
            reply += f" CPU: {telemetry['cpu_percent']:g} %. Memoria: {telemetry['memory_percent']:g} %."
        else:
            reply += " Todavia no hay una muestra vigente del equipo."
        active = len(context["active_alerts"])
        if active:
            reply += f" Hay {active} alerta(s) registradas; revisa su vigencia en Sistema."
        data = {"local_time": now.isoformat(), "timezone": str(self.preferences.zone),
                "telemetry_fresh": context["telemetry_fresh"], "telemetry": telemetry,
                "active_alerts": context["active_alerts"], "external_requests": False}
        panel = self.store.add_panel(owner, "briefing", "Resumen de inicio", data)
        return self.result(reply, panel=panel, kind="briefing", local_time=data["local_time"])

    @staticmethod
    def result(reply: str, *, panel: dict[str, object] | None = None, kind: str = "command",
               commands: list[dict[str, object]] | None = None, **extra: object) -> dict[str, object]:
        return {"reply": reply, "source": kind, "executed": False, "suggested_action": None,
                "ui_commands": commands or [], "panel": panel, **extra}

    async def command(self, owner: str, intent: dict[str, str], scopes: frozenset[str]) -> dict[str, object]:
        kind = intent["type"]
        if kind == "resume_topic":
            if "memory:read" not in scopes:
                raise ValueError("command_scope_required")
            if not self.preferences.value["memory_context_enabled"]:
                return self.result("Activa el uso de recuerdos como contexto en Preferencias para retomar un tema.")
            rows = (await self.services.conversations.page(query=intent["topic"], limit=3))["records"]
            if not rows:
                return self.result("No encontré conversaciones archivadas sobre ese tema.")
            latest = rows[0]
            state = self.store.state(owner, touch=True)
            self.store.remember(owner, state, latest["prompt"], latest["reply"])
            return self.result(
                f"Retomamos desde el turno #{latest['id']}. Me dijiste: {latest['prompt'][:320]}\n"
                f"Mi última respuesta fue: {latest['reply'][:700]}\n¿Con qué parte seguimos?",
                memory_sources=[{"id": item["id"], "date": item["created_at"], "category": item["category"]}
                                for item in rows])
        if kind == "system_review":
            if "telemetry:read" not in scopes:
                raise ValueError("command_scope_required")
            context = self.services.context()
            telemetry = context["telemetry"]
            if not context["telemetry_fresh"] or not isinstance(telemetry, dict):
                return self.result("Revisión local: falta una muestra vigente de telemetría.")
            disk = telemetry.get("disk_free_bytes")
            free = f"{disk / 1024**3:.1f} GiB libres" if isinstance(disk, (int, float)) else "sin lectura"
            return self.result(f"Revisión local. CPU {telemetry['cpu_percent']:g} %. "
                               f"RAM {telemetry['memory_percent']:g} %. Disco: {free}. "
                               f"Alertas activas: {len(context['active_alerts'])}.",
                               panel=self.store.add_panel(owner, "system", "Revisión local del equipo", context))
        if kind == "clear_context":
            self.store.cancel(owner, clear=True)
            self.documents.release(owner)
        elif kind == "stop_response":
            self.store.cancel(owner)
        elif kind == "briefing":
            if "telemetry:read" not in scopes:
                raise ValueError("command_scope_required")
            return await self.briefing(owner)
        elif kind == "media":
            if "actions:prepare" not in scopes:
                raise ValueError("command_scope_required")
            return self.result(intent["reply"], suggested_action=intent["action_id"], requires_confirmation=True)
        elif kind == "navigate":
            permission = {"history": "memory:read", "system": "telemetry:read", "activity": "telemetry:read",
                          "network": "network:read"}.get(intent["view"])
            if permission and permission not in scopes:
                raise ValueError("command_scope_required")
            if intent["view"] == "system":
                panel = self.store.add_panel(owner, "system", "Estado del equipo", self.services.context())
                return self.result(intent["reply"], panel=panel, commands=[{"type": "navigate", "view": "system"}])
        return self.result(intent["reply"], commands=[{key: value for key, value in intent.items() if key != "reply"}])

    async def chat(self, owner: str, prompt: str, scopes: frozenset[str], *, progress: Callable[[str], None] | None = None) -> dict[str, object]:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
            raise ValueError("invalid_prompt")
        decision = coordinate(prompt)
        intent = decision.intent
        if intent and intent["type"] == "resume_topic":
            return await self.run(owner, lambda: self.command(owner, intent, scopes))
        if intent:
            epoch = await self.memory_epoch(scopes)
            result = await self.command(owner, intent, scopes)
            result["memory"] = await self.save_turn(prompt, result["reply"], "command", scopes, epoch)
            return result
        return await self.run(owner, lambda: self.answer(owner, prompt, scopes, progress=progress))

    async def memory_epoch(self, scopes: frozenset[str]) -> int | None:
        """No guardar ni recuperar conversaciones de una sesión sin su permiso."""
        if "memory:write" not in scopes or not self.services.conversations.available:
            return None
        try:
            return await self.services.conversations.epoch()
        except Exception:
            self.services.conversations.errors += 1
            return None

    async def save_turn(self, prompt: str, reply: str, source: str,
                        scopes: frozenset[str], epoch: int | None) -> dict[str, object]:
        """Un fallo de disco/DB se muestra, sin ocultar la respuesta ya generada."""
        saved = False
        if "memory:write" in scopes and epoch is not None:
            try:
                saved = await self.services.conversations.append(prompt, reply, source=source, epoch=epoch)
            except Exception:
                self.services.conversations.errors += 1
        return {"saved": saved, "available": self.services.conversations.available,
                "state": "saved" if saved else "not_saved", "encrypted": saved}

    async def answer(self, owner: str, prompt: str, scopes: frozenset[str], *, progress: Callable[[str], None] | None = None) -> dict[str, object]:
        state = self.store.state(owner, touch=True)
        generation = state.generation
        epoch = await self.memory_epoch(scopes)
        context = self.services.context()
        if "telemetry:read" not in scopes:
            context.update(telemetry=None, telemetry_fresh=False, active_alerts=[], network_local=None)
        context["operator_preferences_untrusted"] = {"display_name": self.preferences.value["display_name"]}
        if self.preferences.value["memory_context_enabled"] and "memory:read" in scopes and self.services.audit.enabled:
            notes = await self.services.audit.memory()
            words = set(re.findall(r"\w{3,}", normalize(prompt)))
            ranked = sorted(((len(words & set(re.findall(r"\w{3,}", normalize(note["text"])))), index, note)
                             for index, note in enumerate(notes)), key=lambda item: (item[0], item[1]), reverse=True)
            context["user_notes_untrusted"] = [{"text": note["text"][:240]}
                                               for score, _, note in ranked[:2] if score > 0]
        if (self.preferences.value["memory_context_enabled"] and "memory:read" in scopes
                and self.services.conversations.available):
            try:
                recalled_epoch, memories = await self.services.conversations.recall(prompt)
                if epoch is None or epoch == recalled_epoch:
                    context["conversation_memory_untrusted"] = memories
            except Exception:
                self.services.conversations.errors += 1
        started = time.monotonic()
        result = await self.services.language.ask(prompt, context=context, query_id=uuid4().hex, history=state.turns, **({"progress": progress} if progress is not None else {}))
        # NOTA DEL OPERADOR: solo un turno completado pasa a la memoria reciente.
        # Un fallo, una cancelacion o una sesion revocada no deja media respuesta.
        self.ensure_current(owner, state, generation)
        saved = await self.save_turn(prompt, result["reply"], "local_llm", scopes, epoch)
        self.ensure_current(owner, state, generation)
        self.store.remember(owner, state, prompt, result["reply"])
        panel = self.store.add_panel(owner, "answer", "Respuesta de T.H.O.R.N", {"text": result["reply"]})
        return {**result, "panel": panel, "ui_commands": [], "source": "local_llm",
                "memory": saved,
                "context_messages": len(state.turns), "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}

    async def map(self, owner: str, query: object, consent: bool,
                  scopes: frozenset[str] = frozenset()) -> dict[str, object]:
        state = self.store.state(owner, touch=True)
        generation = state.generation
        epoch = await self.memory_epoch(scopes)
        async def work() -> dict[str, object]:
            data = await find_places(self.services.internet, query, consent=consent, request_id=uuid4().hex)
            self.ensure_current(owner, state, generation)
            places = data["places"]
            panel = self.store.add_panel(owner, "map", "Lugares y mapas", data, source="wikipedia")
            reply = "No encontre un articulo con coordenadas para ese lugar."
            if places:
                reply = "Lugares con coordenadas verificables: " + "; ".join(p["title"] for p in places) + ". Elige el enlace del mapa correspondiente."
            saved = await self.save_turn(str(query), reply, "map", scopes, epoch)
            self.ensure_current(owner, state, generation)
            return self.result(reply, panel=panel, kind="map", memory=saved, sources=[
                {"title": p["title"], "url": p["map_url"], "excerpt": data["attribution"]} for p in places])
        return await self.run(owner, work)

    async def upload(self, owner: str, raw: bytes, name: str) -> dict[str, object]:
        state = self.store.state(owner, touch=True)
        generation = state.generation
        async def work() -> dict[str, object]:
            data = await self.documents.upload(owner, raw, name, alive=lambda: self.is_current(owner, state, generation))
            self.ensure_current(owner, state, generation)
            panel = self.store.add_panel(owner, "document", data["name"], data)
            reply = f"Documento recibido: {data['name']}. {data['page_count']} pagina(s)."
            if not data["text_available"]:
                reply += " No contiene texto extraible. Usa Leer pagina escaneada en el visor para activar el OCR local."
            return self.result(reply, panel=panel, kind="document", document=data)
        return await self.run(owner, work)

    async def stop(self) -> None:
        if self.sweeper is not None:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        await self.store.stop()
        self.documents.items.clear()
