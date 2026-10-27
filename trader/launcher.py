"""Uygulamayı açar: botları ve paneli penceresiz başlatır, panel penceresini açar.

Penceresiz (pythonw + CREATE_NO_WINDOW): arka planda terminal açılıp kapanmaz.
"""
from __future__ import annotations

import msvcrt
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .safety import HOME

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
RUN_DIR = HOME / "run"
BOTS = ("kaplumbaga", "sorfcu")
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
FLAGS = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_GROUP | NO_WINDOW


def bot_paths(name: str) -> dict:
    if name not in BOTS:
        raise ValueError(f"bilinmeyen bot: {name}")
    return {"lock": RUN_DIR / f"{name}.lock", "shutdown": RUN_DIR / f"{name}.KAPAT",
            "state": HOME / "bots" / f"{name}.json"}


def try_lock(name: str):
    """Botun kilidini almayı dener. Alınırsa dosya nesnesi, alınamazsa None."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    f = open(bot_paths(name)["lock"], "a+")
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def bot_running(name: str) -> bool:
    f = try_lock(name)
    if f is None:
        return True
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    f.close()
    return False


def _spawn(*args):
    subprocess.Popen([str(PYTHONW), str(ROOT / "jarvis.py"), *args], cwd=ROOT, creationflags=FLAGS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def start_bot(name: str) -> str:
    if bot_running(name):
        return "Bot zaten çalışıyor."
    bot_paths(name)["shutdown"].unlink(missing_ok=True)
    _spawn("bot", name)
    return "Bot başlatıldı (sahte para)."


def stop_bot(name: str) -> str:
    if not bot_running(name):
        return "Bot zaten kapalı."
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    bot_paths(name)["shutdown"].write_text("kapat", encoding="utf-8")
    return "Bot 10 saniye içinde güvenli şekilde kapanacak."


def panel_running() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def open_window():
    candidates = [shutil.which("msedge"),
                  os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                  os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")]
    edge = next((c for c in candidates if c and Path(c).exists()), None)
    if edge:   # Uygulama modu: adres çubuğu olmayan ayrı pencere
        subprocess.Popen([edge, f"--app={URL}", "--window-size=1440,960"], creationflags=FLAGS)
    else:
        webbrowser.open(URL)


def main():
    for name in BOTS:
        start_bot(name)
    if not panel_running():
        _spawn("panel")
        for _ in range(40):
            if panel_running():
                break
            time.sleep(0.25)
    open_window()
