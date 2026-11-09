"""
Postagem nas plataformas pelas APIs oficiais, com várias contas (mais de uma por plataforma).

Cada empresa tem UM app de desenvolvedor seu (id + segredo, em Configurações): google = YouTube; meta = Instagram e
Facebook; tiktok = TikTok. "Conectar conta" abre o login da plataforma no navegador (OAuth); o que volta é um token
guardado em config_local.json. A senha nunca passa pelo Studio. Cada login vira uma ou mais contas:
  google -> o canal escolhido no login;  meta -> cada Página do Facebook e o Instagram profissional ligado a ela;
  tiktok -> a conta logada.
Cada conta tem "assuntos" (texto livre): é por ele que o Claude decide em qual conta cada corte vai (sugerir()).

ponytail: escrito pela documentação das APIs, sem contas de teste; o primeiro uso real de cada plataforma é o teste.
Limites conhecidos: app do Google não verificado publica como privado; app do TikTok não auditado só posta privado;
Instagram exige conta profissional ligada a uma Página.
"""
import hashlib
import os
import secrets
import time
import urllib.parse
import uuid

import requests

import config

RETORNO = "http://localhost:7870/api/contas/retorno/{app}"  # cadastre este endereço no app de cada plataforma
GRAPH = "https://graph.facebook.com"  # sem versão: usa a versão padrão do seu app
TIKTOK = "https://open.tiktokapis.com/v2"
NOMES = {"youtube": "YouTube Shorts", "tiktok": "TikTok", "instagram": "Instagram Reels", "facebook": "Facebook Reels"}
ESCOPOS = {
    "google": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
    "meta": "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish,business_management",
    "tiktok": "user.info.basic,video.publish",
}
ESPERA = 600  # segundos esperando a plataforma processar o vídeo


class Erro(RuntimeError):
    pass


def _json(r):
    """Resposta da API -> dict; erro HTTP ou erro no corpo -> Erro com a mensagem da plataforma."""
    try:
        d = r.json()
    except ValueError:
        d = {}
    e = d.get("error") if isinstance(d, dict) else None
    if isinstance(e, dict) and e.get("code") == "ok":  # o TikTok manda "error" sempre; code "ok" = deu certo
        e = None
    if r.status_code >= 400 or e:
        msg = (e.get("message") or e.get("code") if isinstance(e, dict) else e) or d.get("error_description") or r.text[:300]
        raise Erro(f"{r.status_code}: {msg}")
    return d


# ---------------------------------------------------------------- conectar conta (OAuth)

_pendentes = {}  # state -> (app, code_verifier): logins abertos no navegador esperando o retorno


def url_login(app):
    cfg = config.carregar()["apps"][app]
    if not cfg["id"] or not cfg["segredo"]:
        raise Erro("Preencha o id e o segredo do app desta plataforma em Configurações antes de conectar.")
    state, verifier = secrets.token_urlsafe(16), secrets.token_urlsafe(48)
    _pendentes[state] = (app, verifier)
    comum = {"redirect_uri": RETORNO.format(app=app), "response_type": "code", "state": state, "scope": ESCOPOS[app]}
    if app == "google":
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
            dict(comum, client_id=cfg["id"], access_type="offline", prompt="consent select_account"))
    if app == "meta":
        return "https://www.facebook.com/dialog/oauth?" + urllib.parse.urlencode(dict(comum, client_id=cfg["id"]))
    # TikTok (app do tipo Desktop: aceita retorno em localhost e exige PKCE com o SHA-256 em hexadecimal)
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(
        dict(comum, client_key=cfg["id"], code_challenge=hashlib.sha256(verifier.encode()).hexdigest(), code_challenge_method="S256"))


def concluir_login(app, code, state):
    """Troca o código do retorno por tokens e guarda as contas. -> nomes das contas conectadas."""
    pendente, verifier = _pendentes.pop(state, (None, None))
    if pendente != app:
        raise Erro("Esse login não foi iniciado por este Studio (ou expirou). Clique em Conectar de novo.")
    cfg = config.carregar()
    novas = {"google": _contas_google, "meta": _contas_meta, "tiktok": _contas_tiktok}[app](cfg["apps"][app], code, verifier)
    for n in novas:  # reconectar a mesma conta atualiza o token e mantém id e assuntos
        velha = next((c for c in cfg["contas"] if (c["plataforma"], c["alvo"]) == (n["plataforma"], n["alvo"])), None)
        if velha:
            velha.update(nome=n["nome"], tokens=n["tokens"])
        else:
            cfg["contas"].append(dict(n, id=uuid.uuid4().hex[:8], assuntos=""))
    config.salvar(cfg)
    return [f"{NOMES[n['plataforma']]}: {n['nome']}" for n in novas]


def _validade(tok):
    return time.time() + int(tok.get("expires_in", 3600)) - 120


def _contas_google(app, code, _verifier):
    tok = _json(requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": code, "client_id": app["id"], "client_secret": app["segredo"],
        "redirect_uri": RETORNO.format(app="google"), "grant_type": "authorization_code"}))
    canais = _json(requests.get("https://www.googleapis.com/youtube/v3/channels", timeout=30, params={"part": "snippet", "mine": "true"},
                                headers={"Authorization": f"Bearer {tok['access_token']}"})).get("items", [])
    if not canais:
        raise Erro("Essa conta do Google não tem canal no YouTube.")
    return [{"plataforma": "youtube", "alvo": canais[0]["id"], "nome": canais[0]["snippet"]["title"],
             "tokens": {"acesso": tok["access_token"], "renovar": tok.get("refresh_token", ""), "validade": _validade(tok)}}]


def _contas_meta(app, code, _verifier):
    base = {"client_id": app["id"], "client_secret": app["segredo"]}
    curto = _json(requests.get(f"{GRAPH}/oauth/access_token", timeout=30,
                               params=dict(base, redirect_uri=RETORNO.format(app="meta"), code=code)))["access_token"]
    # token de usuário longo -> os tokens das Páginas que saem dele não expiram
    longo = _json(requests.get(f"{GRAPH}/oauth/access_token", timeout=30,
                               params=dict(base, grant_type="fb_exchange_token", fb_exchange_token=curto)))["access_token"]
    paginas = _json(requests.get(f"{GRAPH}/me/accounts", timeout=30, params={
        "fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100, "access_token": longo})).get("data", [])
    if not paginas:
        raise Erro("Nenhuma Página do Facebook foi liberada no login. O Instagram precisa ser profissional e estar ligado a uma Página.")
    out = []
    for p in paginas:
        out.append({"plataforma": "facebook", "alvo": p["id"], "nome": p["name"], "tokens": {"acesso": p["access_token"]}})
        ig = p.get("instagram_business_account")
        if ig:
            out.append({"plataforma": "instagram", "alvo": ig["id"], "nome": "@" + ig.get("username", ig["id"]), "tokens": {"acesso": p["access_token"]}})
    return out


def _tiktok_token(app, **dados):
    tok = _json(requests.post(f"{TIKTOK}/oauth/token/", timeout=30, data=dict(dados, client_key=app["id"], client_secret=app["segredo"])))
    if "access_token" not in tok:
        raise Erro(tok.get("error_description") or str(tok)[:300])
    return {"acesso": tok["access_token"], "renovar": tok.get("refresh_token", ""), "validade": _validade(tok)}, tok.get("open_id", "")


def _contas_tiktok(app, code, verifier):
    tokens, open_id = _tiktok_token(app, code=code, grant_type="authorization_code",
                                    redirect_uri=RETORNO.format(app="tiktok"), code_verifier=verifier)
    u = _json(requests.get(f"{TIKTOK}/user/info/", timeout=30, params={"fields": "open_id,display_name"},
                           headers={"Authorization": f"Bearer {tokens['acesso']}"}))["data"]["user"]
    return [{"plataforma": "tiktok", "alvo": open_id or u.get("open_id", ""), "nome": u.get("display_name", "TikTok"), "tokens": tokens}]


# ---------------------------------------------------------------- postar

def _token(conta):
    """Token de acesso válido da conta (renova e guarda se venceu)."""
    t = conta["tokens"]
    if "validade" not in t or time.time() < t["validade"]:
        return t["acesso"]
    if not t.get("renovar"):
        raise Erro("O acesso desta conta venceu. Conecte a conta de novo em Configurações.")
    cfg = config.carregar()
    if conta["plataforma"] == "youtube":
        app = cfg["apps"]["google"]
        tok = _json(requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
            "client_id": app["id"], "client_secret": app["segredo"], "refresh_token": t["renovar"], "grant_type": "refresh_token"}))
        novo = {"acesso": tok["access_token"], "renovar": t["renovar"], "validade": _validade(tok)}
    else:
        novo, _ = _tiktok_token(cfg["apps"]["tiktok"], grant_type="refresh_token", refresh_token=t["renovar"])
    conta["tokens"] = novo
    for c in cfg["contas"]:
        if c["id"] == conta["id"]:
            c["tokens"] = novo
    config.salvar(cfg)
    return novo["acesso"]


def _esperar(consultar, o_que):
    """consultar() -> True (pronto) | False (ainda processando); levanta Erro se a plataforma recusar."""
    fim = time.time() + ESPERA
    while time.time() < fim:
        if consultar():
            return
        time.sleep(5)
    raise Erro(f"{o_que} não terminou de processar em {ESPERA // 60} minutos; confira na plataforma antes de postar de novo.")


def _youtube(conta, video, titulo, descricao, publico):
    h = {"Authorization": f"Bearer {_token(conta)}"}
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos", timeout=60,
                      params={"uploadType": "resumable", "part": "snippet,status"},
                      headers=dict(h, **{"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(os.path.getsize(video))}),
                      json={"snippet": {"title": titulo.replace("<", "").replace(">", "")[:100], "description": descricao[:4900], "categoryId": "22"},
                            "status": {"privacyStatus": "public" if publico else "private", "selfDeclaredMadeForKids": False}})
    _json(r)
    with open(video, "rb") as f:
        d = _json(requests.put(r.headers["Location"], data=f, headers=dict(h, **{"Content-Type": "video/mp4"}), timeout=3600))
    return f"https://youtube.com/shorts/{d['id']}"


def _enviar_meta(url, token, video):
    with open(video, "rb") as f:
        _json(requests.post(url, data=f, timeout=3600, headers={"Authorization": f"OAuth {token}", "offset": "0",
                                                                "file_size": str(os.path.getsize(video))}))


def _instagram(conta, video, titulo, descricao, publico):
    if not publico:
        raise Erro("O Instagram não tem postagem privada pela API.")
    tk, ig = _token(conta), conta["alvo"]
    c = _json(requests.post(f"{GRAPH}/{ig}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": f"{titulo}\n\n{descricao}".strip()[:2200], "access_token": tk}))
    _enviar_meta(c["uri"], tk, video)

    def pronto():
        st = _json(requests.get(f"{GRAPH}/{c['id']}", timeout=30, params={"fields": "status_code,status", "access_token": tk}))
        if st.get("status_code") == "ERROR":
            raise Erro(f"O Instagram recusou o vídeo: {st.get('status', '')}")
        return st.get("status_code") == "FINISHED"
    _esperar(pronto, "O Instagram")
    post = _json(requests.post(f"{GRAPH}/{ig}/media_publish", timeout=60, data={"creation_id": c["id"], "access_token": tk}))
    return _json(requests.get(f"{GRAPH}/{post['id']}", timeout=30, params={"fields": "permalink", "access_token": tk})).get("permalink", "")


def _facebook(conta, video, titulo, descricao, publico):
    tk, pagina = _token(conta), conta["alvo"]
    ini = _json(requests.post(f"{GRAPH}/{pagina}/video_reels", timeout=60, data={"upload_phase": "start", "access_token": tk}))
    _enviar_meta(ini["upload_url"], tk, video)
    _json(requests.post(f"{GRAPH}/{pagina}/video_reels", timeout=60, data={
        "upload_phase": "finish", "video_id": ini["video_id"], "video_state": "PUBLISHED" if publico else "DRAFT",
        "description": f"{titulo}\n\n{descricao}".strip(), "access_token": tk}))
    return f"https://www.facebook.com/reel/{ini['video_id']}"


def partes_tiktok(tamanho, parte=10 * 1024 * 1024):
    """Regra do TikTok: até 64 MB vai inteiro; acima, partes iguais e a última leva o resto. -> [(início, fim inclusivo)]"""
    if tamanho <= 64 * 1024 * 1024:
        return [(0, tamanho - 1)]
    n = tamanho // parte
    return [(k * parte, (k + 1) * parte - 1 if k < n - 1 else tamanho - 1) for k in range(n)]


def _tiktok(conta, video, titulo, descricao, publico):
    h = {"Authorization": f"Bearer {_token(conta)}", "Content-Type": "application/json; charset=UTF-8"}
    opcoes = _json(requests.post(f"{TIKTOK}/post/publish/creator_info/query/", headers=h, timeout=30))["data"].get("privacy_level_options", [])
    nivel = "PUBLIC_TO_EVERYONE" if publico else "SELF_ONLY"
    if nivel not in opcoes:
        raise Erro(f"Esta conta do TikTok não aceita postagem {'pública' if publico else 'privada'} pela API "
                   f"(aceita: {', '.join(opcoes) or 'nada'}). App não auditado pelo TikTok só posta como privado.")
    tamanho = os.path.getsize(video)
    partes = partes_tiktok(tamanho)
    ini = _json(requests.post(f"{TIKTOK}/post/publish/video/init/", headers=h, timeout=60, json={
        "post_info": {"title": f"{titulo} {descricao}".strip()[:2200], "privacy_level": nivel,
                      "disable_duet": False, "disable_comment": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho, "chunk_size": partes[0][1] + 1, "total_chunk_count": len(partes)}}))["data"]
    with open(video, "rb") as f:
        for a, b in partes:
            r = requests.put(ini["upload_url"], data=f.read(b - a + 1), timeout=3600,
                             headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {a}-{b}/{tamanho}"})
            if r.status_code >= 400:
                raise Erro(f"{r.status_code}: envio para o TikTok falhou: {r.text[:200]}")

    def pronto():
        st = _json(requests.post(f"{TIKTOK}/post/publish/status/fetch/", headers=h, timeout=30, json={"publish_id": ini["publish_id"]}))["data"]
        if st.get("status") == "FAILED":
            raise Erro(f"O TikTok recusou o vídeo: {st.get('fail_reason', '')}")
        return st.get("status") == "PUBLISH_COMPLETE"
    _esperar(pronto, "O TikTok")
    return "https://www.tiktok.com/"  # a API não devolve o link do post; ele aparece no perfil da conta


def postar(conta, video, titulo, descricao, publico=True):
    """Publica o vídeo na conta. -> link do post (ou "" se a plataforma não informa). Levanta Erro com o motivo."""
    try:
        return {"youtube": _youtube, "instagram": _instagram, "facebook": _facebook, "tiktok": _tiktok}[conta["plataforma"]](
            conta, video, titulo, descricao, publico)
    except requests.RequestException as e:
        raise Erro(f"sem resposta da plataforma: {e}")
    except KeyError as e:
        raise Erro(f"resposta inesperada da plataforma (faltou {e})")


# ---------------------------------------------------------------- o Claude escolhe a conta e escreve o texto

PROMPT = """Você cuida da postagem de cortes (vídeos curtos verticais) em várias contas. Cada conta tem um tema; cada corte
deve ir só para as contas cujo tema combina com o assunto dele. Normalmente é uma conta por plataforma (a do tema
certo); se nenhuma conta de uma plataforma combinar, não poste nessa plataforma. Corte que não combina com nenhuma
conta fica com a lista de contas vazia.

Para cada corte devolva:
- contas: os ids das contas onde postar;
- titulo: até 90 caracteres, chamativo, fiel ao que é dito, sem clickbait falso;
- descricao: 1 ou 2 frases e de 3 a 6 hashtags relevantes no final.

CONTAS (conta sem tema escrito aceita qualquer assunto):
{contas}

CORTES:
{cortes}"""
ESQUEMA = {"type": "object", "required": ["cortes"], "properties": {"cortes": {"type": "array", "items": {
    "type": "object", "required": ["id", "contas", "titulo", "descricao"],
    "properties": {"id": {"type": "string"}, "contas": {"type": "array", "items": {"type": "string"}},
                   "titulo": {"type": "string"}, "descricao": {"type": "string"}}}}}}


def sugerir(cortes, modelo="sonnet"):
    """cortes: [{"id", "titulo", "texto"}] -> {id: {"contas": [ids], "titulo", "descricao"}} (só contas que existem)."""
    import claude
    contas = config.carregar()["contas"]
    if not contas:
        raise Erro("Nenhuma conta conectada. Conecte as contas em Configurações.")
    r = claude.perguntar(PROMPT.format(
        contas="\n".join(f"- id={c['id']} | {NOMES[c['plataforma']]} | {c['nome']} | tema: {c.get('assuntos') or '(qualquer)'}" for c in contas),
        cortes="\n\n".join(f"id={c['id']} | título atual: {c['titulo']}\n{c['texto'][:1500]}" for c in cortes)), ESQUEMA, modelo)
    validas, pedidos = {c["id"] for c in contas}, {c["id"] for c in cortes}
    return {c["id"]: {"contas": [i for i in c["contas"] if i in validas], "titulo": c["titulo"].strip()[:100], "descricao": c["descricao"].strip()}
            for c in r["cortes"] if c["id"] in pedidos}


def selftest():
    MB = 1024 * 1024
    assert partes_tiktok(30 * MB) == [(0, 30 * MB - 1)]
    p = partes_tiktok(75 * MB)
    assert len(p) == 7 and p[0] == (0, 10 * MB - 1) and p[-1] == (60 * MB, 75 * MB - 1) and all(a2 == b1 + 1 for (_, b1), (a2, _) in zip(p, p[1:]))

    class R:  # resposta falsa
        def __init__(self, status, corpo): self.status_code, self._c, self.text = status, corpo, str(corpo)
        def json(self): return self._c
    assert _json(R(200, {"data": 1, "error": {"code": "ok", "message": ""}})) == {"data": 1, "error": {"code": "ok", "message": ""}}
    for r, trecho in ((R(400, {"error": {"message": "token inválido"}}), "token inválido"), (R(200, {"error": {"code": "spam_risk", "message": ""}}), "spam_risk"),
                      (R(401, {"error": "invalid_grant", "error_description": "x"}), "invalid_grant")):
        try:
            _json(r)
            assert False, r._c
        except Erro as e:
            assert trecho in str(e), e
    print("selftest ok")


if __name__ == "__main__":
    selftest()
