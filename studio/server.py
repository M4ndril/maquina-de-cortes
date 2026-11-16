"""
Estúdio de cortes: app web local em etapas (entrada -> assuntos -> layouts -> exportação).
Rodar: python studio/server.py  (abre http://127.0.0.1:7870)

Estado de cada live em VIRALS/<live>/projeto.json. Tarefas longas rodam em studio/tarefas.py, uma por vez.
"""
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser

import uvicorn
from fastapi import Body, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tarefas  # noqa: E402  (mesma leitura do projeto.json que as tarefas usam)

VIRALS = os.path.join(ROOT, "VIRALS")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORTA = 7870

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


_CODIGO = tuple(os.path.normcase(os.path.join(ROOT, d)) + os.sep for d in ("studio", "scripts"))
_versoes = {}


@app.middleware("http")
async def recarregar_codigo(request, call_next):
    """Código do Studio alterado com ele aberto (studio/*.py e scripts/*.py, menos este arquivo): recarrega antes de
    responder, sem precisar reiniciar. (As tarefas longas já rodam num processo novo a cada vez.)"""
    import importlib
    for nome, mod in list(sys.modules.items()):
        arq = os.path.normcase(getattr(mod, "__file__", None) or "")
        if not arq.startswith(_CODIGO) or arq == os.path.normcase(__file__) or not os.path.exists(arq):
            continue
        t = os.path.getmtime(arq)
        if _versoes.setdefault(nome, t) != t:
            _versoes[nome] = t
            try:
                importlib.reload(mod)
            except Exception as e:  # código com erro: segue com a versão anterior e avisa no console
                print(f"[Studio] não consegui recarregar {nome}: {e}")
    return await call_next(request)


@app.middleware("http")
async def sem_cache_na_interface(request, call_next):
    """Navegador sempre confere se a interface mudou (responde 304 se não): atualização do app aparece sem Ctrl+F5."""
    r = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        r.headers["Cache-Control"] = "no-cache"
    return r

# ---------------------------------------------------------------- tarefa em andamento (uma por vez: a GPU é uma só)

TAREFA = {"status": "livre"}
_lock = threading.Lock()


def _ler_saida(proc):
    for linha in proc.stdout:
        linha = linha.rstrip()
        if linha.startswith("@@progresso "):
            _, p, texto = linha.split(" ", 2)
            TAREFA.update(progresso=int(p), texto=texto)
        elif linha.startswith("@@projeto "):
            TAREFA["projeto"] = os.path.basename(linha[len("@@projeto "):])
        elif linha.strip():
            TAREFA["log"] = (TAREFA.get("log", []) + [linha])[-300:]
    proc.wait()
    if TAREFA.get("status") == "rodando":
        TAREFA["status"] = "ok" if proc.returncode == 0 else "erro"


def iniciar_tarefa(tipo, projeto, args):
    with _lock:
        if TAREFA.get("status") == "rodando":
            raise HTTPException(409, f"Já existe uma tarefa rodando: {TAREFA['texto']}")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "studio", "tarefas.py"), tipo] + args, cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        TAREFA.clear()
        TAREFA.update(status="rodando", tipo=tipo, projeto=projeto, progresso=0, texto="Iniciando...", log=[], pid=proc.pid)
        threading.Thread(target=_ler_saida, args=(proc,), daemon=True).start()
    return {"ok": True}


@app.get("/api/tarefa")
def ver_tarefa():
    return {k: v for k, v in TAREFA.items() if k != "pid"}


@app.post("/api/tarefa/parar")
def parar_tarefa():
    if TAREFA.get("status") == "rodando":
        TAREFA["status"] = "parada"
        subprocess.run(["taskkill", "/PID", str(TAREFA["pid"]), "/T", "/F"], capture_output=True)
    return {"ok": True}


# ---------------------------------------------------------------- projetos

def pasta_de(nome):
    p = os.path.realpath(os.path.join(VIRALS, nome))
    if os.path.dirname(p) != os.path.realpath(VIRALS) or not os.path.isdir(p):
        raise HTTPException(404, "Projeto não encontrado")
    return p


def ler_projeto(pasta):
    proj = dict(tarefas.carregar(pasta), nome=os.path.basename(pasta))
    proj.setdefault("assuntos", [])
    proj["arquivos"] = {k: os.path.exists(os.path.join(pasta, f))
                        for k, f in (("video", "input.mp4"), ("proxy", "proxy.mp4"), ("transcricao", "input.tsv"))}
    if proj["arquivos"]["transcricao"]:
        proj["etapa"] = max(proj.get("etapa", 1), 2)
    if any(a.get("layout") for a in proj["assuntos"]):  # análise de layout em dia com os trechos atuais?
        import cortes
        segs = cortes.carregar_transcricao(pasta)
        for a in proj["assuntos"]:
            if a.get("layout"):
                a["layout_estado"] = "ok" if tarefas.layout_valido(pasta, a, segs)[0] else "desatualizado"
    return proj


def salvar_projeto(pasta, proj):
    proj = {k: v for k, v in proj.items() if k != "arquivos"}
    tmp = os.path.join(pasta, "projeto.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(pasta, "projeto.json"))


# ---------------------------------------------------------------- conta do YouTube (ver studio/youtube.py)

@app.get("/api/youtube")
def youtube_estado():
    import youtube
    return youtube.estado()


@app.post("/api/youtube/entrar")
def youtube_entrar():
    """Abre o Firefox do Studio na página de login do YouTube; quem entra na conta é o usuário."""
    import youtube
    try:
        youtube.abrir_login()
    except FileNotFoundError:
        raise HTTPException(404, "Firefox não encontrado. Instale o Firefox (é o navegador que o Studio consegue usar para a sessão).")
    return {"ok": True}


@app.post("/api/youtube/cookies")
async def youtube_cookies(arquivo: UploadFile):
    """Alternativa: cookies.txt (formato Netscape) exportado de um navegador logado no YouTube."""
    import youtube
    dados = (await arquivo.read()).decode("utf-8", "ignore")
    # linhas de cookie têm 7 campos separados por tab; as HttpOnly começam com "#HttpOnly_" (não são comentário)
    if not any(".youtube.com" in l.split("\t")[0] for l in dados.splitlines() if l.count("\t") >= 6):
        raise HTTPException(400, "Esse arquivo não tem cookies do youtube.com. Exporte com o YouTube aberto e logado na sua conta.")
    with open(youtube.COOKIES_TXT, "w", encoding="utf-8") as f:
        f.write(dados)
    return youtube.estado()


@app.delete("/api/youtube")
def youtube_sair():
    import youtube
    youtube.sair()
    return youtube.estado()


@app.get("/api/projetos")
def listar():
    out = []
    for nome in sorted(os.listdir(VIRALS), key=lambda n: -os.path.getmtime(os.path.join(VIRALS, n))):
        pasta = os.path.join(VIRALS, nome)
        if os.path.isdir(pasta) and os.path.exists(os.path.join(pasta, "input.mp4")):
            p = ler_projeto(pasta)
            out.append({"nome": nome, "etapa": p.get("etapa", 1), "assuntos": len(p["assuntos"]), "arquivos": p["arquivos"],
                        "data": os.path.getmtime(os.path.join(pasta, "input.mp4"))})  # quando a live foi baixada
    return out


@app.get("/api/projetos/{nome}")
def abrir(nome: str):
    return ler_projeto(pasta_de(nome))


@app.post("/api/projetos/novo")
def novo_por_link(url: str = Body(..., embed=True)):
    if not re.match(r"https?://", url.strip()):
        raise HTTPException(400, "Cole um link começando com http:// ou https://")
    return iniciar_tarefa("entrada", None, ["-", "--url", url.strip()])


@app.post("/api/projetos/upload")
async def novo_por_upload(arquivo: UploadFile):
    nome = re.sub(r'[\\/*?:"<>|]', "", os.path.splitext(arquivo.filename)[0]).strip() or "Video"
    pasta = os.path.join(VIRALS, nome)
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "input.mp4"), "wb") as f:
        while chunk := await arquivo.read(8 * 1024 * 1024):
            f.write(chunk)
    return iniciar_tarefa("entrada", nome, [pasta])


@app.post("/api/projetos/{nome}/entrada")
def refazer_entrada(nome: str, retranscrever: bool = Body(False, embed=True)):
    """retranscrever: apaga a transcrição e faz de novo no idioma das Configurações (os tempos das frases mudam)."""
    return iniciar_tarefa("entrada", nome, [pasta_de(nome)] + (["--retranscrever"] if retranscrever else []))


@app.put("/api/projetos/{nome}/assuntos")
def salvar_assuntos(nome: str, assuntos: list = Body(...)):
    pasta = pasta_de(nome)
    proj = ler_projeto(pasta)
    proj["assuntos"] = [{k: v for k, v in a.items() if k != "layout_estado"} for a in assuntos]
    salvar_projeto(pasta, proj)
    return {"ok": True}


@app.post("/api/projetos/{nome}/assuntos/gerar")
def gerar_assuntos(nome: str, modelo: str = Body("sonnet"), modo: str = Body("novo"),
                   duracao_min: float = Body(0), duracao_max: float = Body(0)):
    """duracao_min/max: limite de duração dos cortes em segundos (0 = sem esse lado; os dois 0 = limite desligado)."""
    if modo not in ("novo", "mais"):
        raise HTTPException(400, "modo inválido")
    if duracao_min < 0 or duracao_max < 0 or (duracao_max and duracao_min > duracao_max):
        raise HTTPException(400, "Limite de duração inválido: o mínimo não pode passar do máximo")
    return iniciar_tarefa("assuntos", nome, [pasta_de(nome), "--modelo", modelo, "--modo", modo,
                                             "--min", str(duracao_min), "--max", str(duracao_max)])


@app.post("/api/projetos/{nome}/versoes")
def salvar_versao(nome: str, rotulo: str = Body("", embed=True)):
    """Guarda a lista de assuntos atual como uma versão."""
    pasta = pasta_de(nome)
    proj = tarefas.carregar(pasta)
    tarefas.guardar_versao(proj, rotulo.strip() or "salva por você")
    tarefas.salvar(pasta, proj)
    return {"ok": True}


@app.post("/api/projetos/{nome}/versoes/{indice}/restaurar")
def restaurar_versao(nome: str, indice: int):
    """Volta para uma versão; a lista atual vira versão antes (nada se perde)."""
    pasta = pasta_de(nome)
    proj = tarefas.carregar(pasta)
    versoes = proj.get("versoes") or []
    if not 0 <= indice < len(versoes):
        raise HTTPException(404, "Versão não encontrada")
    escolhida = versoes.pop(indice)
    tarefas.guardar_versao(proj, "antes de restaurar")
    proj["assuntos"] = escolhida["assuntos"]
    tarefas.salvar(pasta, proj)
    return {"ok": True}


@app.get("/api/projetos/{nome}/transcricao")
def transcricao(nome: str):
    """Frases da live: [[início, fim, texto], ...] (o índice é a identidade da frase no resumo)."""
    import cortes
    return [[round(s["start"], 2), round(s["end"], 2), s.get("text", "").strip()]
            for s in cortes.carregar_transcricao(pasta_de(nome))]


NIVEIS_RESUMO = {
    "leve": "Tire só o que sobra: repetições, vícios de fala, frases incompletas, interrupções e cumprimentos. Mantenha todo o resto.",
    "medio": "Tire também digressões, exemplos secundários e falas que repetem uma ideia já dita com outras palavras.",
    "forte": "Fique só com o essencial: a abertura, os pontos principais e a conclusão.",
}
PROMPT_RESUMO = """Você edita cortes rápidos para TikTok/Reels de um canal de lives e vídeos longos (podcast, entrevista, debate, notícias, aula).
Abaixo estão as frases de UM assunto da live, numeradas, com a duração de cada uma. O editor escolheu à mão onde
esse assunto começa e termina: a primeira frase é a abertura e a última é o fechamento. Use isso como referência
de começo e fim; você só pode escolher frases desta lista.

Escolha as frases que formam a versão RESUMIDA desse assunto. {nivel}

O mais importante é o corte continuar fazendo sentido para quem não viu a live:
- cada frase mantida precisa se ligar à anterior; se uma frase depende de outra ("isso", "ele", a resposta a uma pergunta), mantenha as duas;
- não deixe o raciocínio pela metade: na dúvida entre tirar e quebrar o sentido, mantenha;
- mantenha a ordem original.

FRASES:
{frases}"""
ESQUEMA_RESUMO = {"type": "object", "required": ["frases"],
                  "properties": {"frases": {"type": "array", "items": {"type": "integer"}}}}


def _frases_do_assunto(a, segs):
    """Índices das frases dentro do assunto (pelo meio da frase), sem as que o usuário desligou."""
    fora = set(a.get("desligadas") or [])
    return [i for i, s in enumerate(segs) if a["inicio"] <= (s["start"] + s["end"]) / 2 <= a["fim"] and i not in fora]


@app.post("/api/projetos/{nome}/assuntos/{aid}/resumir")
def resumir(nome: str, aid: str, modelo: str = Body("sonnet"), nivel: str = Body("medio")):
    import claude
    import cortes
    pasta = pasta_de(nome)
    proj, a = _assunto(pasta, aid)
    segs = cortes.carregar_transcricao(pasta)
    idx = _frases_do_assunto(a, segs)
    linhas = "\n".join(f"[{i}] ({segs[i]['end'] - segs[i]['start']:.1f}s) {segs[i].get('text', '').strip()}" for i in idx)
    try:
        resposta = claude.perguntar(PROMPT_RESUMO.format(nivel=NIVEIS_RESUMO.get(nivel, NIVEIS_RESUMO["medio"]), frases=linhas),
                                    ESQUEMA_RESUMO, modelo)
    except RuntimeError as e:
        raise HTTPException(502, f"Erro no Claude: {e}")
    escolhidas = sorted({i for i in resposta["frases"] if i in idx})  # só dentro da seleção do usuário
    if not escolhidas:
        raise HTTPException(502, "O Claude não escolheu nenhuma frase.")
    a["resumo"] = {"frases": escolhidas, "nivel": nivel}
    tarefas.salvar(pasta, proj)
    return a


PROMPT_SUBCORTES = """Você edita cortes rápidos para TikTok/Reels de um canal de lives e vídeos longos (podcast, entrevista, debate, notícias, aula).
Abaixo está UM assunto da live que o editor já separou, com as frases numeradas e o tempo de cada uma.
Encontre, DENTRO dele, cortes menores que funcionem sozinhos: um argumento, uma resposta, uma revelação, uma frase de impacto
com o contexto mínimo para ser entendida. Quantos houver de bom (pode ser nenhum); não force.
Cada corte vai de uma frase inicial até uma frase final desta lista; os cortes não se sobrepõem.
Dê um título chamativo em português e diga em poucas palavras por que funciona sozinho.

FRASES:
{frases}"""
ESQUEMA_SUBCORTES = {"type": "object", "required": ["cortes"], "properties": {"cortes": {"type": "array", "items": {
    "type": "object", "required": ["titulo", "inicio_frase", "fim_frase"],
    "properties": {"titulo": {"type": "string"}, "inicio_frase": {"type": "integer"}, "fim_frase": {"type": "integer"},
                   "motivo": {"type": "string"}}}}}}


@app.post("/api/projetos/{nome}/assuntos/{aid}/subcortes")
def sugerir_subcortes(nome: str, aid: str, modelo: str = Body("sonnet", embed=True)):
    """Cortes menores dentro do assunto, sugeridos pelo Claude (só frases dentro da seleção). Não salva: a tela adiciona."""
    import claude
    import cortes
    pasta = pasta_de(nome)
    _, a = _assunto(pasta, aid)
    segs = cortes.carregar_transcricao(pasta)
    idx = _frases_do_assunto(a, segs)
    linhas = "\n".join(f"[{i}] {_hms(segs[i]['start'])} {segs[i].get('text', '').strip()}" for i in idx)
    try:
        r = claude.perguntar(PROMPT_SUBCORTES.format(frases=linhas), ESQUEMA_SUBCORTES, modelo)
    except RuntimeError as e:
        raise HTTPException(502, f"Erro no Claude: {e}")
    dentro, out = set(idx), []
    for c in sorted(r["cortes"], key=lambda c: c["inicio_frase"]):
        i, f = c["inicio_frase"], c["fim_frase"]
        if i not in dentro or f not in dentro or f < i:
            continue
        ini, fim = max(segs[i]["start"], a["inicio"]), min(segs[f]["end"], a["fim"])
        if fim - ini >= 1 and not any(ini < o["fim"] and fim > o["inicio"] for o in out):
            out.append({"titulo": c["titulo"], "inicio": round(ini, 2), "fim": round(fim, 2), "motivo": c.get("motivo", "")})
    return out


# ---------------------------------------------------------------- chat com o Claude (etapa 2)

SISTEMA_CHAT = """Você é o assistente de edição do editor de cortes de um canal de lives e vídeos longos (podcast, entrevista, debate, notícias, aula).
O usuário transforma a live em cortes curtos (TikTok/Reels). Você conhece a transcrição inteira, com as frases numeradas,
e a lista atual de assuntos (cortes). Ajude a lapidar os assuntos: dividir, juntar, ajustar início/fim, achar assuntos
novos, renomear, resumir. Também pode só responder perguntas sobre a live.

Regras:
- Limites de assunto são SEMPRE números de frase: começa no início de uma frase e termina no fim de outra.
- Cada corte precisa se sustentar sozinho para quem não viu a live (começo claro, conclusão).
- Dividir = "alterar" o original + "criar" o novo. Juntar = "alterar" um + "apagar" o outro.
- "resumir" escolhe frases DENTRO do assunto: o essencial, sem repetições, vícios de fala e digressões.
- Só proponha operações quando o usuário pedir mudanças; para perguntas, operacoes = [].

Responda APENAS com JSON, sem markdown:
{"resposta": "texto curto em português para o usuário",
 "operacoes": [
   {"op": "criar", "titulo": "...", "inicio_frase": N, "fim_frase": M},
   {"op": "alterar", "id": "...", "titulo": "... (opcional)", "inicio_frase": N, "fim_frase": M},
   {"op": "apagar", "id": "..."},
   {"op": "resumir", "id": "...", "frases": [N, ...]},
   {"op": "desfazer_resumo", "id": "..."},
   {"op": "incluir", "id": "...", "incluir": true}
 ]}
Em "alterar", mande só os campos que mudam."""


def _hms(t):
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _contexto_assuntos(assuntos, segs):
    linhas = []
    for a in assuntos:
        idx = [i for i, s in enumerate(segs) if a["inicio"] <= (s["start"] + s["end"]) / 2 <= a["fim"]]
        faixa = f"frases {idx[0]}-{idx[-1]}" if idx else "sem frases"
        resumo = f" | resumo: frases {a['resumo']['frases']}" if a.get("resumo") else ""
        dentro = f" | corte dentro do assunto id={a['pai']}" if a.get("pai") else ""
        linhas.append(f"- id={a['id']}{dentro} | {faixa} ({_hms(a['inicio'])}-{_hms(a['fim'])}) | exportar: "
                      f"{'sim' if a.get('incluir', True) else 'não'}{resumo} | título: {a['titulo']}")
    return "\n".join(linhas) or "(nenhum assunto ainda)"


ESQUEMA_CHAT = {
    "type": "object", "required": ["resposta", "operacoes"],
    "properties": {
        "resposta": {"type": "string"},
        "operacoes": {"type": "array", "items": {
            "type": "object", "required": ["op"],
            "properties": {
                "op": {"enum": ["criar", "alterar", "apagar", "resumir", "desfazer_resumo", "incluir"]},
                "id": {"type": "string"}, "titulo": {"type": "string"},
                "inicio_frase": {"type": "integer"}, "fim_frase": {"type": "integer"},
                "frases": {"type": "array", "items": {"type": "integer"}}, "incluir": {"type": "boolean"}}}}}}


def _claude_chat(pasta, conteudo, modelo, sessao, nova):
    """-> (ok, {"resposta", "operacoes"} | texto do erro)"""
    import claude
    # As instruções vão na 1ª mensagem, que fica na conversa. Com --system-prompt longo, o --resume
    # perdia a conversa: as mensagens seguintes não viam mais a transcrição.
    entrada = f"{SISTEMA_CHAT}\n\n{conteudo}" if nova else conteudo
    try:  # cwd fixo na pasta da live: o Claude guarda/acha a sessão por pasta
        return True, claude.perguntar(entrada, ESQUEMA_CHAT, modelo, cwd=pasta, extra=["--session-id" if nova else "--resume", sessao])
    except RuntimeError as e:
        return False, str(e)


@app.post("/api/projetos/{nome}/chat")
def chat(nome: str, mensagem: str = Body(...), modelo: str = Body("sonnet")):
    import uuid
    import cortes
    pasta = pasta_de(nome)
    proj = tarefas.carregar(pasta)
    conversa = proj.setdefault("chat", {"mensagens": []})
    segs = cortes.carregar_transcricao(pasta)
    assuntos_txt = _contexto_assuntos(proj.get("assuntos", []), segs)
    pedido = f"ASSUNTOS ATUAIS:\n{assuntos_txt}\n\nPEDIDO DO USUÁRIO:\n{mensagem}"

    ok, saida = False, None
    if conversa.get("sessao") and conversa.get("v") == 2:  # continua a conversa: a transcrição já está no contexto
        ok, saida = _claude_chat(pasta, pedido, modelo, conversa["sessao"], nova=False)
    if not ok:                  # primeira mensagem (ou sessão perdida): manda a live inteira
        conversa["sessao"], conversa["v"] = str(uuid.uuid4()), 2  # v2: instruções dentro da conversa
        frases = "\n".join(f"[{i}] {_hms(s['start'])} {s.get('text', '').strip()}" for i, s in enumerate(segs))
        ok, saida = _claude_chat(pasta, f"TRANSCRIÇÃO DA LIVE (frases numeradas):\n{frases}\n\n{pedido}",
                                 modelo, conversa["sessao"], nova=True)
    if not ok:
        raise HTTPException(502, f"Erro no Claude: {str(saida)[:400]}")
    resposta, ops = str(saida.get("resposta", "")), [o for o in saida.get("operacoes", []) if isinstance(o, dict)]
    conversa["mensagens"] += [{"de": "voce", "texto": mensagem},
                              {"de": "claude", "texto": resposta, "operacoes": ops, "estado": "pendente" if ops else None}]
    tarefas.salvar(pasta, proj)
    return {"indice": len(conversa["mensagens"]) - 1, **conversa["mensagens"][-1]}


@app.put("/api/projetos/{nome}/chat/{indice}")
def chat_estado(nome: str, indice: int, estado: str = Body(..., embed=True)):
    pasta = pasta_de(nome)
    proj = tarefas.carregar(pasta)
    proj["chat"]["mensagens"][indice]["estado"] = estado
    tarefas.salvar(pasta, proj)
    return {"ok": True}


@app.post("/api/projetos/{nome}/chat/nova")
def chat_nova(nome: str):
    pasta = pasta_de(nome)
    proj = tarefas.carregar(pasta)
    proj["chat"] = {"mensagens": []}
    tarefas.salvar(pasta, proj)
    return {"ok": True}


# ---------------------------------------------------------------- layouts (etapa 3)

@app.post("/api/projetos/{nome}/layouts")
def analisar_layouts(nome: str, ids: list = Body(..., embed=True)):
    if not ids:
        raise HTTPException(400, "Nenhum assunto para analisar")
    return iniciar_tarefa("layouts", nome, [pasta_de(nome), "--ids", ",".join(ids)])


def _assunto(pasta, aid):
    proj = tarefas.carregar(pasta)
    a = next((x for x in proj["assuntos"] if x["id"] == aid), None)
    if a is None:
        raise HTTPException(404, "Assunto não encontrado")
    return proj, a


@app.put("/api/projetos/{nome}/assuntos/{aid}/layout")
def salvar_layout(nome: str, aid: str, plans: list = Body(..., embed=True)):
    pasta = pasta_de(nome)
    proj, a = _assunto(pasta, aid)
    if not a.get("layout"):
        raise HTTPException(409, "Analise os layouts deste assunto primeiro")
    a["layout"]["plans"] = plans
    tarefas.salvar(pasta, proj)
    return {"ok": True}


@app.post("/api/projetos/{nome}/assuntos/{aid}/quadro")
def quadro_layout(nome: str, aid: str, t: float = Body(...), plans: list = Body(...)):
    """Prévia da montagem vertical no instante t, com os planos que estão na tela (editados ou não)."""
    from fastapi.responses import Response
    from scripts import channel_layout
    video = os.path.join(pasta_de(nome), "layouts", f"{aid}.mp4")
    if not os.path.exists(video):
        raise HTTPException(404, "Vídeo do assunto não analisado")
    return Response(channel_layout.quadro(video, plans, t, channel_layout.load_layout()), media_type="image/jpeg")


# ---------------------------------------------------------------- legenda: estilo do canal e correções de texto

@app.get("/api/legenda")
def ver_estilo():
    import legenda
    return {"estilo": legenda.carregar(), "padrao": legenda.PADRAO, "fontes": legenda.fontes()}


@app.put("/api/legenda")
def salvar_estilo(estilo: dict = Body(..., embed=True)):
    import legenda
    return {"estilo": legenda.salvar(estilo)}


@app.get("/api/legenda/google")
def buscar_fonte_google(q: str = ""):
    import legenda
    if len(q.strip()) < 2:
        return []
    try:
        return legenda.buscar_google(q)
    except OSError as e:
        raise HTTPException(502, f"Não consegui acessar o Google Fonts: {e}")


@app.post("/api/legenda/google")
def baixar_fonte_google(familia: str = Body(..., embed=True)):
    import legenda
    try:
        nomes = legenda.baixar_google(familia)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except OSError as e:
        raise HTTPException(502, f"Não consegui baixar do Google Fonts: {e}")
    return {"adicionadas": nomes, "fontes": legenda.fontes()}


@app.post("/api/legenda/fonte")
async def enviar_fonte(arquivo: UploadFile):
    import legenda
    try:
        nome = legenda.guardar_fonte(arquivo.filename, await arquivo.read())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"adicionadas": [nome], "fontes": legenda.fontes()}


@app.post("/api/projetos/{nome}/assuntos/{aid}/legenda/previa")
def previa_legenda(nome: str, aid: str, t: float = Body(...), estilo: dict = Body(...)):
    from fastapi.responses import Response
    import legenda
    pasta = pasta_de(nome)
    try:
        return Response(legenda.previa(pasta, _assunto(pasta, aid)[1], t, estilo), media_type="image/jpeg")
    except LookupError as e:
        raise HTTPException(409, str(e))


@app.get("/api/projetos/{nome}/assuntos/{aid}/legenda")
def texto_legenda(nome: str, aid: str):
    """Palavras da legenda do assunto: [[ref, início no corte, texto, texto original ou null]]."""
    import cortes
    pasta = pasta_de(nome)
    a = _assunto(pasta, aid)[1]
    segs = cortes.carregar_transcricao(pasta)
    trechos = (a.get("layout") or {}).get("trechos_video") or cortes.trechos_do_assunto(a, segs)
    # palavras apagadas (texto vazio) também aparecem, para poder desfazer
    corr = tarefas.carregar(pasta).get("correcoes") or {}
    apagadas = {r for r, v in corr.get("palavras", {}).items() if not v.strip()}
    for r in apagadas:
        si, wi = map(int, r.split(":"))
        segs[si]["words"][wi]["word"] = "∅"  # marcador só para listar; não vai para a legenda
    return [[w["ref"], round(w["start"], 2), "" if w["ref"] in apagadas else w["word"], w.get("original")]
            for s in cortes.legenda_dos_trechos(segs, trechos)["segments"] for w in s["words"]]


@app.put("/api/projetos/{nome}/correcoes/palavra")
def corrigir_palavra(nome: str, ref: str = Body(...), texto: str = Body(...)):
    import cortes
    pasta = pasta_de(nome)
    si, wi = map(int, ref.split(":"))
    proj = tarefas.carregar(pasta)
    corr = proj.setdefault("correcoes", {})
    sem_esta = cortes.aplicar_correcoes(cortes.carregar_transcricao(pasta, corrigida=False), {"trocas": corr.get("trocas", [])})
    palavras = corr.setdefault("palavras", {})
    if texto.strip() == sem_esta[si]["words"][wi]["word"].strip():
        palavras.pop(ref, None)  # voltou ao texto de antes: não é mais correção
    else:
        palavras[ref] = texto.strip()
    tarefas.salvar(pasta, proj)
    return {"ok": True}


@app.put("/api/projetos/{nome}/correcoes/trocas")
def salvar_trocas(nome: str, trocas: list = Body(..., embed=True)):
    """Trocas de palavra em toda a live; devolve quantas vezes cada uma aparece."""
    import cortes
    pasta = pasta_de(nome)
    trocas = [{"de": t["de"].strip(), "para": t.get("para", "").strip()} for t in trocas if t.get("de", "").strip()]
    proj = tarefas.carregar(pasta)
    proj.setdefault("correcoes", {})["trocas"] = trocas
    tarefas.salvar(pasta, proj)
    palavras = [w["word"] for s in cortes.carregar_transcricao(pasta, corrigida=False) for w in s.get("words", [])]
    out = []
    for t in trocas:
        rx = cortes.regra_troca(t["de"])
        out.append(dict(t, n=sum(1 for p in palavras if rx.search(p))))
    return out


@app.post("/api/projetos/{nome}/exportar")
def exportar(nome: str, ids: list = Body(...), formatos: list = Body(...)):
    if not ids or not formatos:
        raise HTTPException(400, "Escolha pelo menos um assunto e um formato")
    return iniciar_tarefa("exportar", nome, [pasta_de(nome), "--ids", ",".join(ids), "--formatos", ",".join(formatos)])


# ---------------------------------------------------------------- configurações (idioma, apps e contas)

@app.get("/api/config")
def ver_config():
    import config
    return config.publico(config.carregar())


@app.put("/api/config")
def salvar_config(mudancas: dict = Body(...)):
    import config
    config.atualizar(mudancas)
    return ver_config()


@app.get("/api/contas/conectar/{app_nome}")
def conectar_conta(app_nome: str):
    """Leva o navegador para o login da plataforma; ela devolve para /api/contas/retorno."""
    import config
    import postagem
    if app_nome not in config.APPS:
        raise HTTPException(404, "Plataforma desconhecida")
    try:
        return RedirectResponse(postagem.url_login(app_nome))
    except postagem.Erro as e:
        raise HTTPException(400, str(e))


@app.get("/api/contas/retorno/{app_nome}")
def retorno_conta(app_nome: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    import postagem
    from urllib.parse import quote
    try:
        if not code:
            raise postagem.Erro(error_description or error or "a plataforma não devolveu o código de acesso")
        aviso = "Conectado: " + ", ".join(postagem.concluir_login(app_nome, code, state))
    except (postagem.Erro, KeyError, OSError) as e:
        aviso = f"Não consegui conectar: {e}"
    return RedirectResponse(f"/#/config?aviso={quote(aviso)}")


@app.delete("/api/contas/{cid}")
def remover_conta(cid: str):
    """Tira a conta do Studio (o acesso em si você revoga nas configurações da plataforma)."""
    import config
    cfg = config.carregar()
    cfg["contas"] = [c for c in cfg["contas"] if c["id"] != cid]
    config.salvar(cfg)
    return ver_config()


# ---------------------------------------------------------------- postagem (etapa 5)

@app.post("/api/projetos/{nome}/postagem/sugerir")
def sugerir_postagem(nome: str, ids: list = Body(...), modelo: str = Body("sonnet")):
    """O Claude escolhe as contas de cada corte e escreve título e descrição. Você confere antes de postar."""
    import cortes
    import postagem
    pasta = pasta_de(nome)
    proj = tarefas.carregar(pasta)
    segs = cortes.carregar_transcricao(pasta)
    alvos = [a for a in proj["assuntos"] if a["id"] in ids]
    info = [{"id": a["id"], "titulo": a["titulo"],
             "texto": " ".join(segs[i].get("text", "").strip() for i in (a.get("resumo") or {}).get("frases") or _frases_do_assunto(a, segs))}
            for a in alvos]
    try:
        sugestoes = postagem.sugerir(info, modelo)
    except (postagem.Erro, RuntimeError) as e:
        raise HTTPException(502, f"Erro no Claude: {e}")
    for a in alvos:
        if a["id"] in sugestoes:
            a["postagem"] = dict(sugestoes[a["id"]], feitas=(a.get("postagem") or {}).get("feitas", {}))
    tarefas.salvar(pasta, proj)
    return {a["id"]: a.get("postagem") for a in alvos}


@app.put("/api/projetos/{nome}/assuntos/{aid}/postagem")
def salvar_postagem(nome: str, aid: str, titulo: str = Body(""), descricao: str = Body(""), contas: list = Body(...)):
    pasta = pasta_de(nome)
    proj, a = _assunto(pasta, aid)
    a["postagem"] = {"titulo": titulo.strip(), "descricao": descricao.strip(), "contas": contas,
                     "feitas": (a.get("postagem") or {}).get("feitas", {})}
    tarefas.salvar(pasta, proj)
    return {"ok": True}


@app.post("/api/projetos/{nome}/postar")
def postar(nome: str, ids: list = Body(...), publico: bool = Body(True)):
    if not ids:
        raise HTTPException(400, "Nenhum corte escolhido")
    return iniciar_tarefa("postar", nome, [pasta_de(nome), "--ids", ",".join(ids)] + ([] if publico else ["--privado"]))


@app.post("/api/projetos/{nome}/abrir-pasta")
def abrir_pasta(nome: str, sub: str = Body("entrega", embed=True)):
    alvo = os.path.realpath(os.path.join(pasta_de(nome), sub))
    if not alvo.startswith(pasta_de(nome)) or not os.path.exists(alvo):
        raise HTTPException(404, "Pasta não encontrada")
    os.startfile(alvo)  # app local: abre no Explorer
    return {"ok": True}


@app.get("/media/{nome}/{caminho:path}")
def media(nome: str, caminho: str):
    pasta = pasta_de(nome)
    alvo = os.path.realpath(os.path.join(pasta, caminho))
    if not alvo.startswith(pasta + os.sep) or not os.path.isfile(alvo):
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(alvo)  # suporta range: o player pula para qualquer ponto


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


if __name__ == "__main__":
    os.makedirs(VIRALS, exist_ok=True)
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORTA}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORTA, log_level="warning")
