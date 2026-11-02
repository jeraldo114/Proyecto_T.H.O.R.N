"""Ventana Windows WebView2; el servidor asincrono vive en su propio hilo."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.logging_config import queued_logging
from core.version import RELEASE

logger = logging.getLogger("thorn.desktop")


def configure_startup(enabled: bool) -> None:
    """Conserva la entrada HKCU original para no duplicar el inicio con Windows."""
    if os.name != "nt":
        raise RuntimeError("startup_requires_windows")
    import winreg

    executable = Path(sys.executable).resolve()
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    from core.paths import compiled, data_root
    arguments = [str(executable), '--data-dir', str(data_root()), '--background'] if compiled() else [str(executable), str(Path(__file__).resolve()), '--background']
    command = subprocess.list2cmdline(arguments)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, "THORN Bastion 8", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "THORN Bastion 8")
            except FileNotFoundError:
                pass


def _listener() -> socket.socket:
    """Reserva un puerto efimero antes de anunciar la URL; evita TOCTOU del puerto."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


class BackendHost:
    """Posee exactamente un servidor, socket y loop. No se conecta a otro proceso."""

    def __init__(self, *, without_database: bool) -> None:
        self.without_database = without_database
        self.ready = threading.Event()
        self.error: str | None = None
        self.url = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_requested = threading.Event()
        self._server: Any = None
        self._application: Any = None
        self._native_identity: str | None = None
        # NOTA DEL OPERADOR: WebView2 necesita su hilo de interfaz. El backend
        # tiene otro hilo con su propio loop; ninguno debe esperar I/O del otro
        # dentro de un callback de la ventana.
        self._thread = threading.Thread(target=self._run, name="thorn.backend", daemon=False)

    def start(self) -> str:
        self._thread.start()
        if not self.ready.wait(25) or self.error:
            self.stop()
            raise RuntimeError(self.error or "desktop_startup_timeout")
        return self.url

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self.error = type(exc).__name__
            logger.warning("El servidor de escritorio fallo (%s).", self.error)
        finally:
            self.ready.set()

    async def _serve(self) -> None:
        import uvicorn
        from serve import _load_application

        self._loop = asyncio.get_running_loop()
        application = await asyncio.to_thread(_load_application, without_database=self.without_database)
        self._application = application
        if self._stop_requested.is_set():
            return
        listener = await asyncio.to_thread(_listener)
        if self._stop_requested.is_set():
            listener.close()
            return
        self._server = uvicorn.Server(uvicorn.Config(
            application, host="127.0.0.1", port=listener.getsockname()[1], lifespan="on",
            loop="asyncio", workers=1, log_config=None, access_log=False,
            limit_concurrency=64, timeout_graceful_shutdown=10,
        ))
        self.url = f"http://127.0.0.1:{listener.getsockname()[1]}/"
        serving = asyncio.create_task(self._server.serve(sockets=[listener]))
        try:
            while not self._server.started and not serving.done():
                await asyncio.sleep(0.05)
            if not self._server.started:
                raise RuntimeError("desktop_backend_unavailable")
            self.ready.set()
            await serving
        finally:
            self._server.should_exit = True
            if not serving.done():
                await serving
            listener.close()

    def request_stop(self) -> None:
        self._stop_requested.set()
        if self._loop and self._server and self._loop.is_running():
            self._loop.call_soon_threadsafe(setattr, self._server, "should_exit", True)

    def native_session(self) -> dict[str, object]:
        """Canal interno del proceso: nunca entrega la autoridad persistente al HUD."""
        async def issue() -> dict[str, object]:
            runtime = self._application._runtime
            if self._native_identity:
                runtime.sessions.revoke_identity(self._native_identity)
            payload = runtime.sessions.issue()
            self._native_identity = payload["session_id"]
            return payload
        if self._loop is None:
            raise RuntimeError("desktop_backend_unavailable")
        return asyncio.run_coroutine_threadsafe(issue(), self._loop).result(timeout=5)

    def stop(self) -> None:
        self.request_stop()
        if self._thread.ident is not None:
            self._thread.join(75)
            if self._thread.is_alive():
                raise RuntimeError("desktop_shutdown_incomplete")


def create_tray(window: Any) -> Any:
    """El menu nativo solo controla la visibilidad y cierre de la ventana propia."""
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), "#101315")
    draw = ImageDraw.Draw(image)
    draw.polygon([(12, 12), (52, 12), (52, 22), (37, 22), (37, 53), (27, 53), (27, 22), (12, 22)], fill="#5ad7de")
    tray = pystray.Icon("thorn-b9", image, f"T.H.O.R.N | {RELEASE}", menu=pystray.Menu(
        pystray.MenuItem("Mostrar T.H.O.R.N", lambda: window.show(), default=True),
        pystray.MenuItem("Ocultar", lambda: window.hide()),
        pystray.MenuItem("Salir", lambda: window.destroy()),
    ))
    tray.run_detached()
    return tray


def run_desktop(*, without_database: bool, smoke_test: bool, background: bool) -> int:
    """Ejecuta WebView2 en el hilo GUI; no expone una API Python a JavaScript."""
    import webview

    backend = BackendHost(without_database=without_database)
    tray: Any = None
    smoke_ok = threading.Event()
    window_closed = threading.Event()
    try:
        url = backend.start()
        # Los informes usan el dialogo nativo de guardado, nunca rutas enviadas por JS.
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        window = webview.create_window(
            f"T.H.O.R.N | {RELEASE}", url, width=1440, height=900, min_size=(760, 620),
            background_color="#101315", text_select=True, hidden=smoke_test or background,
        )
        window.events.closed += backend.request_stop
        window.events.closed += window_closed.set
        def loaded() -> None:
            if window.get_current_url() != url:
                return
            credential = backend.native_session()
            window.run_js("window.dispatchEvent(new CustomEvent('thorn-native-session', {detail: "
                          + json.dumps(credential) + "}));")
            credential = {}
            if smoke_test:
                try:
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        if window.evaluate_js("document.querySelector('#session-label').textContent === 'Conectado'"):
                            result = window.evaluate_js("""(() => {
                                const canvas = document.querySelector('#core-canvas');
                                const copy = document.createElement('canvas'); copy.width=canvas.width; copy.height=canvas.height;
                                const ctx=copy.getContext('2d'); ctx.drawImage(canvas,0,0);
                                const pixels=ctx.getImageData(0,0,copy.width,copy.height).data;
                                let lit=0; for(let i=0;i<pixels.length;i+=4) if(pixels[i]+pixels[i+1]+pixels[i+2]>100) lit++;
                                return {lit, clock: document.querySelector('#clock-time').textContent,
                                  microphone_supported: Boolean(window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.AudioContext && window.AudioWorkletNode)};
                            })()""")
                            if result and result["lit"] > 1000 and result["clock"] != "--:--:--" and result["microphone_supported"] and tray and tray.visible:
                                smoke_ok.set()
                                break
                        time.sleep(0.2)
                finally:
                    window.destroy()

        window.events.loaded += loaded
        try:
            tray = create_tray(window)
        except Exception as exc:
            logger.warning("Bandeja no disponible (%s).", type(exc).__name__)
            if background:
                background = False
                window.hidden = False
        def watchdog() -> None:
            if smoke_test:
                if not window_closed.wait(25):
                    window.destroy()
        webview.start(func=watchdog, gui="edgechromium", debug=False, private_mode=True)
        if smoke_test:
            print("Desktop smoke:", "OK (WebView2, sesion, reloj, canvas, bandeja)" if smoke_ok.is_set() else "FAILED")
            return 0 if smoke_ok.is_set() else 1
        return 0
    finally:
        if tray:
            tray.stop()
        backend.stop()


def main() -> int:
    if sys.argv[1:] == ["--document-worker"]:
        from core.document_worker import main as document_main
        return document_main()
    parser = argparse.ArgumentParser(description=f"T.H.O.R.N {RELEASE} - escritorio Windows")
    parser.add_argument("--sin-db", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="Comprueba una ventana oculta y la cierra.")
    parser.add_argument("--background", action="store_true", help="Arranca oculto, accesible desde la bandeja.")
    parser.add_argument('--data-dir', type=Path, help='Carpeta de configuracion, modelos y datos privados.')
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument("--enable-startup", action="store_true")
    startup.add_argument("--disable-startup", action="store_true")
    args = parser.parse_args()
    if args.data_dir:
        os.environ['THORN_DATA_DIR'] = str(args.data_dir.resolve())
    if os.name != "nt":
        parser.error("El lanzador de escritorio requiere Windows y WebView2.")
    if args.enable_startup or args.disable_startup:
        configure_startup(args.enable_startup)
        print("Inicio con Windows:", "habilitado" if args.enable_startup else "deshabilitado")
        return 0
    with queued_logging():
        try:
            return run_desktop(without_database=args.sin_db, smoke_test=args.smoke_test, background=args.background)
        except Exception as exc:
            logger.error("No se pudo abrir T.H.O.R.N (%s).", type(exc).__name__)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
