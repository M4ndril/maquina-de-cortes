"""
Configurações locais do Studio (config_local.json, fora do git): idioma da transcrição, apps de desenvolvedor
das plataformas e as contas conectadas para postagem. Tem segredos e tokens: a tela só recebe a versão de publico().
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQUIVO = os.path.join(ROOT, "config_local.json")
IDIOMAS = ("pt", "en", "es", "auto")
APPS = ("google", "meta", "tiktok")  # um app de desenvolvedor por empresa: google = YouTube; meta = Instagram + Facebook


def carregar():
    cfg = {}
    if os.path.exists(ARQUIVO):
        with open(ARQUIVO, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.setdefault("idioma", "pt")  # idioma falado nos vídeos; "auto" = o Whisper adivinha
    cfg.setdefault("contas", [])
    for app in APPS:
        cfg.setdefault("apps", {}).setdefault(app, {"id": "", "segredo": ""})
    return cfg


def salvar(cfg):
    with open(ARQUIVO + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(ARQUIVO + ".tmp", ARQUIVO)


def publico(cfg):
    """O que a tela vê: nada de chave, segredo ou token (só se já foram preenchidos)."""
    return {
        "idioma": cfg["idioma"],
        "apps": {k: {"id": v["id"], "segredo": bool(v["segredo"])} for k, v in cfg["apps"].items()},
        "contas": [{k: c.get(k, "") for k in ("id", "plataforma", "nome", "assuntos")} for c in cfg["contas"]],
    }


def atualizar(mudancas):
    """Mudanças vindas da tela: {"idioma", "apps": {app: {"id", "segredo"}}, "contas": [{"id", "assuntos"}]}.
    Campo de segredo vazio = não mexe (a tela nunca recebe o valor para devolver)."""
    cfg = carregar()
    if mudancas.get("idioma") in IDIOMAS:
        cfg["idioma"] = mudancas["idioma"]
    for app, v in (mudancas.get("apps") or {}).items():
        if app in APPS:
            cfg["apps"][app]["id"] = str(v.get("id", cfg["apps"][app]["id"])).strip()
            if v.get("segredo"):
                cfg["apps"][app]["segredo"] = str(v["segredo"]).strip()
    assuntos = {c["id"]: str(c.get("assuntos", "")) for c in mudancas.get("contas") or []}
    for c in cfg["contas"]:
        if c["id"] in assuntos:
            c["assuntos"] = assuntos[c["id"]]
    salvar(cfg)
    return cfg
