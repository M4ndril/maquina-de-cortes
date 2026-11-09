"""
Conta do YouTube para os downloads. O Studio tem um perfil de Firefox só dele (navegador_youtube/): você entra
na conta numa janela normal do Firefox e o yt-dlp lê a sessão desse perfil a cada download (Firefox é o único
navegador que deixa outro programa ler os cookies; Chrome/Edge criptografam). Alternativa: cookies.txt enviado.
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERFIL = os.path.join(ROOT, "navegador_youtube")
COOKIES_TXT = os.path.join(ROOT, "cookies.txt")
LOGIN = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"


def firefox():
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        exe = os.path.join(os.environ.get(var, ""), "Mozilla Firefox", "firefox.exe")
        if os.path.isfile(exe):
            return exe
    return shutil.which("firefox")


def abrir_login():
    """Janela do Firefox com o perfil do Studio na página de login do YouTube (quem digita a senha é você)."""
    exe = firefox()
    if not exe:
        raise FileNotFoundError("Firefox não encontrado")
    os.makedirs(PERFIL, exist_ok=True)
    subprocess.Popen([exe, "-profile", PERFIL, "-no-remote", LOGIN])


def logado():
    """O perfil do Studio tem sessão do YouTube? (cópia do banco: o Firefox aberto mantém o original travado)"""
    db = os.path.join(PERFIL, "cookies.sqlite")
    if not os.path.exists(db):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for suf in ("", "-wal"):  # cookies recentes ficam no -wal até o Firefox consolidar
                if os.path.exists(db + suf):
                    shutil.copy2(db + suf, os.path.join(tmp, "c.sqlite" + suf))
            con = sqlite3.connect(os.path.join(tmp, "c.sqlite"))
            try:
                n = con.execute("select count(*) from moz_cookies where host like '%youtube.com' "
                                "and name in ('LOGIN_INFO', 'SAPISID', '__Secure-3PSID')").fetchone()[0]
            finally:
                con.close()
        return n > 0
    except (OSError, sqlite3.Error):
        return False


def estado():
    if logado():
        return {"conectado": True, "como": "firefox", "firefox": True}
    if os.path.exists(COOKIES_TXT):
        return {"conectado": True, "como": "arquivo", "firefox": bool(firefox())}
    return {"conectado": False, "firefox": bool(firefox())}


def sessoes():
    """Opções de sessão para o yt-dlp, na ordem de preferência."""
    out = []
    if logado():
        out.append({"cookiesfrombrowser": ("firefox", PERFIL)})
    if os.path.exists(COOKIES_TXT):
        out.append({"cookiefile": COOKIES_TXT})
    return out


def sair():
    shutil.rmtree(PERFIL, ignore_errors=True)
    if os.path.exists(COOKIES_TXT):
        os.remove(COOKIES_TXT)
