"""
Tarefas longas do Estúdio, executadas num processo separado pelo server.py (uma por vez; GPU liberada no fim).
Cada linha "@@progresso <0-100> <texto>" atualiza a barra de progresso; "@@projeto <pasta>" informa a pasta criada.

  python studio/tarefas.py entrada  <pasta|-> [--url URL]
  python studio/tarefas.py assuntos <pasta> [--modelo sonnet] [--modo novo|mais] [--min 30] [--max 90]
  python studio/tarefas.py broll    <pasta> --ids id1,id2 [--modelo sonnet]
  python studio/tarefas.py exportar <pasta> --ids id1,id2 --formatos legenda,sem_legenda,editavel
  python studio/tarefas.py postar   <pasta> --ids id1,id2 [--privado]
"""
import argparse
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

VIRALS = os.path.join(ROOT, "VIRALS")
PARALELO = 3  # cortes montados ao mesmo tempo (medido: 3 já ocupam o encoder; GeForce aceita até 8 sessões NVENC)


def progresso(p, texto):
    print(f"@@progresso {int(p)} {texto}", flush=True)


class BarraDoLog:
    """Enquanto ativo, linhas do log que casam com 'padrao' (ex.: "Progress: 42.5%" do WhisperX, que só imprime)
    viram progresso na barra. O log continua igual; a barra muda no máximo 1x por ponto percentual."""
    def __init__(self, padrao, ao_progredir):
        import re
        self.rx, self.cb, self.ultimo = re.compile(padrao), ao_progredir, -1

    def write(self, texto):
        self.saida.write(texto)
        for m in self.rx.finditer(texto):
            pct = float(m.group(1))
            if int(pct) != self.ultimo:
                self.ultimo = int(pct)
                self.cb(pct)
        return len(texto)

    def flush(self):
        self.saida.flush()

    def __enter__(self):
        self.saida, sys.stdout = sys.stdout, self
        return self

    def __exit__(self, *erro):
        sys.stdout = self.saida


def carregar(pasta):
    caminho = os.path.join(pasta, "projeto.json")
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    return {"nome": os.path.basename(pasta), "assuntos": [], "etapa": 1}


def salvar(pasta, proj):
    tmp = os.path.join(pasta, "projeto.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(pasta, "projeto.json"))


# ---------------------------------------------------------------- 1. entrada

def entrada(pasta, url=None, retranscrever=False):
    if url:
        progresso(2, "Baixando o vídeo...")
        from scripts import download_video
        import youtube  # conta do YouTube conectada na página inicial (evita "confirme que não é um robô")
        _, pasta = download_video.download(
            url, base_root=VIRALS, download_subs=False,  # legenda do YouTube não é usada: a transcrição é nossa
            sessoes=youtube.sessoes(),
            ao_progredir=lambda frac, mbs, eta: progresso(2 + 26 * frac, f"Baixando o vídeo... {frac:.0%} ({mbs:.1f} MB/s)"))
        pasta = os.path.abspath(pasta)
        print(f"@@projeto {pasta}", flush=True)
    proj = carregar(pasta)
    proj.setdefault("origem", url or "upload")
    salvar(pasta, proj)

    video = os.path.join(pasta, "input.mp4")
    proxy = os.path.join(pasta, "proxy.mp4")
    if not os.path.exists(proxy):
        progresso(30, "Gerando cópia leve (360p) para o editor...")
        # keyframe a cada 1 s: arrastar pelo vídeo no navegador fica instantâneo
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vf", "scale=-2:360", "-c:v", "h264_nvenc",
                        "-preset", "p1", "-b:v", "700k", "-g", "30", "-c:a", "aac", "-b:a", "64k",
                        "-movflags", "+faststart", proxy], check=True)

    import config
    idioma = config.carregar()["idioma"]
    if retranscrever:  # os três saem juntos: um input.srt sozinho seria lido como legenda pronta, só para alinhar
        for ext in ("srt", "tsv", "json"):
            if os.path.exists(os.path.join(pasta, f"input.{ext}")):
                os.remove(os.path.join(pasta, f"input.{ext}"))
    progresso(50, f"Transcrevendo em {idioma} (pode levar alguns minutos)...")
    from scripts import transcribe_video
    with BarraDoLog(r"Progress: ([\d.]+)%", lambda pct: progresso(50 + 0.48 * pct, f"Transcrevendo... {pct:.0f}%")):
        transcribe_video.transcribe(video, "large-v3-turbo", project_folder=pasta, language=None if idioma == "auto" else idioma)

    proj = carregar(pasta)
    proj["etapa"] = max(proj.get("etapa", 1), 2)
    salvar(pasta, proj)
    progresso(100, "Pronto: vídeo baixado e transcrito")


# ---------------------------------------------------------------- 2. assuntos

PROMPT_ASSUNTOS = """Você é o editor de cortes de um canal de notícias e debate político ao vivo. Encontre, na transcrição da live abaixo, os ASSUNTOS COMPLETOS que funcionam sozinhos como vídeo curto (TikTok / Instagram Reels / YouTube Shorts).

Cada linha da transcrição é uma fala com seu tempo exato em segundos: `[início-fim] texto`.

O QUE É UM BOM CORTE
1. ASSUNTO COMPLETO: começa onde o assunto começa e termina onde ele é concluído. Não existe limite de duração: o corte dura o que o assunto durar. Nunca corte no meio de um raciocínio, de uma frase ou de uma resposta.
2. INÍCIO: na fala que abre o assunto. Prefira começar com algo forte (afirmação, pergunta, notícia). Não comece com vícios de fala ("então", "né", "é...") nem no meio de uma frase.
3. FIM: na fala que fecha o assunto (conclusão, punchline, resposta final), antes da conversa mudar de tema. Não inclua a transição para o próximo tema.
4. SOZINHO: quem nunca viu a live tem que entender. Se o trecho depende de algo dito antes ("como ele falou", "isso aí"), comece antes, onde o contexto é dado.
5. Ignore leitura de comentários do chat, agradecimentos a apoiadores, pedidos de like/PIX e intervalos.
6. Os cortes não podem se sobrepor.

Liste TODOS os assuntos que dariam um bom corte, quantos houver: não existe número fixo, é você que diz quantos cortes essa live tem. Não force cortes fracos para aumentar a lista.
Para cada um: título chamativo em português; inicio = INÍCIO da primeira fala do corte e fim = FIM da última fala, copiando os números da transcrição; motivo = por que funciona sozinho; nota de 0 a 100.

TRANSCRIÇÃO:
{transcricao}"""
ESQUEMA_ASSUNTOS = {"type": "object", "required": ["assuntos"], "properties": {"assuntos": {"type": "array", "items": {
    "type": "object", "required": ["titulo", "inicio", "fim"],
    "properties": {"titulo": {"type": "string"}, "inicio": {"type": "number"}, "fim": {"type": "number"},
                   "motivo": {"type": "string"}, "nota": {"type": "number"}}}}}}


SEM_LIMITE = "Não existe limite de duração: o corte dura o que o assunto durar."
COM_LIMITE = ("LIMITE DE DURAÇÃO (obrigatório): cada corte dura {faixa}. Assunto mais longo que isso: use o trecho mais forte "
              "dele que caiba no limite e se sustente sozinho, ou divida em cortes independentes. Assunto que não cabe no limite não entra.")
TOLERANCIA = 0.1  # o encaixe na frase mais próxima mexe nas bordas: corte até 10% fora do limite ainda vale


def regra_duracao(dmin, dmax):
    """Frase do prompt para o limite de duração (segundos; 0 = sem esse lado). Sem limite nenhum = regra original."""
    if not dmin and not dmax:
        return SEM_LIMITE
    faixa = (f"entre {dmin:g} e {dmax:g} segundos" if dmin and dmax else
             f"no mínimo {dmin:g} segundos" if dmin else f"no máximo {dmax:g} segundos")
    return COM_LIMITE.format(faixa=faixa)


def dentro_do_limite(a, dmin, dmax):
    d = a["fim"] - a["inicio"]
    return d >= dmin * (1 - TOLERANCIA) and (not dmax or d <= dmax * (1 + TOLERANCIA))


MAX_VERSOES = 15


def guardar_versao(proj, nome):
    """Cópia da lista de assuntos atual (com seleções, resumos e layouts) em proj["versoes"], a mais nova primeiro."""
    if proj.get("assuntos"):
        proj.setdefault("versoes", []).insert(0, {"nome": nome, "data": time.strftime("%d/%m %H:%M"),
                                                  "assuntos": json.loads(json.dumps(proj["assuntos"]))})
        del proj["versoes"][MAX_VERSOES:]


def assuntos(pasta, modelo="sonnet", modo="novo", dmin=0, dmax=0):
    """modo "novo": lista nova no lugar da atual; "mais": mantém os assuntos atuais e acrescenta outros.
    Nos dois, a lista de antes fica guardada como versão. dmin/dmax: limite de duração dos cortes em segundos (0 = desligado)."""
    import claude
    import cortes
    progresso(10, f"Claude ({modelo}) lendo a live inteira...")
    segs = cortes.carregar_transcricao(pasta)
    falas = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}" for s in segs)
    existentes = [a for a in carregar(pasta).get("assuntos", []) if not a.get("pai")] if modo == "mais" else []
    pedido = PROMPT_ASSUNTOS.replace(SEM_LIMITE, regra_duracao(dmin, dmax)).replace("{transcricao}", falas)
    if existentes:
        pedido += ("\n\nO editor JÁ TEM estes cortes (não repita nem sobreponha; encontre OUTROS assuntos da live):\n"
                   + "\n".join(f"[{a['inicio']:.1f}-{a['fim']:.1f}] {a['titulo']}" for a in existentes))
    # ponytail: a live vai inteira num pedido só (3 h ~ 210 mil caracteres); dividir em partes se passar do contexto do Claude
    r = claude.perguntar(pedido, ESQUEMA_ASSUNTOS, modelo)
    novos = cortes.encaixar_assuntos(r["assuntos"], segs, maximo=len(r["assuntos"]),
                                     ocupados=[(a["inicio"], a["fim"]) for a in existentes])
    fora = [a for a in novos if not dentro_do_limite(a, dmin, dmax)]
    if fora:  # o Claude nem sempre obedece: o limite ligado é garantido aqui
        print(f"[Assuntos] {len(fora)} corte(s) fora do limite de duração descartado(s): " + "; ".join(a["titulo"] for a in fora))
        novos = [a for a in novos if a not in fora]
    novos = [{"id": uuid.uuid4().hex[:8], "titulo": a["titulo"], "inicio": a["inicio"], "fim": a["fim"],
              "incluir": True, "nota": a.get("nota", 0), "motivo": a.get("motivo", "")} for a in novos]
    proj = carregar(pasta)  # relê: o usuário pode ter mexido na lista enquanto o Claude lia
    guardar_versao(proj, "antes de encontrar mais" if modo == "mais" else "antes de gerar do zero")
    proj["assuntos"] = (proj.get("assuntos", []) + novos) if modo == "mais" else novos
    proj["assuntos"].sort(key=lambda a: (a["inicio"], bool(a.get("pai"))))
    proj["etapa"] = max(proj.get("etapa", 2), 2)
    salvar(pasta, proj)
    progresso(100, f"{len(novos)} assuntos novos" + (" (os seus continuam na lista)" if modo == "mais" else " encontrados"))


# ---------------------------------------------------------------- 3. layouts

def layout_valido(pasta, a, segmentos):
    """Análise de layout salva no assunto, se ainda bate com os trechos atuais (senão None)."""
    import cortes
    lay = a.get("layout")
    video = os.path.join(pasta, "layouts", f"{a['id']}.mp4")
    if lay and os.path.exists(video) and lay.get("assinatura") == cortes.assinatura(cortes.trechos_do_assunto(a, segmentos)):
        return lay, video
    return None, None


@functools.cache
def rostos():
    from scripts import channel_layout
    return channel_layout.init_face_app()


def ler_amostras(pasta, cfg):
    """Cache das amostras de layout da live (por quadro da live), compartilhado por todos os assuntos."""
    from scripts import channel_layout
    caminho = os.path.join(pasta, "layouts", "amostras.json")
    chave = {"v": channel_layout.AMOSTRA_V, "sample_fps": cfg.get("sample_fps", 8)}
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
        if all(d.get(k) == v for k, v in chave.items()):
            return d
    return dict(chave, a={})


def salvar_amostras(pasta, d):
    caminho = os.path.join(pasta, "layouts", "amostras.json")
    with open(caminho + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"))
    os.replace(caminho + ".tmp", caminho)


def analisar_layout(pasta, a, segmentos, cfg, amostras):
    """Corta o assunto em layouts/<id>.mp4 e grava a análise de layout nele. -> (layout, vídeo)
    Só analisa o que ainda não foi visto na live; correções do usuário continuam onde a fala continua."""
    from scripts import channel_layout
    import cortes
    trechos = cortes.trechos_do_assunto(a, segmentos)
    finais = cortes.trechos_finais(pasta, a, segmentos)
    video = os.path.join(pasta, "layouts", f"{a['id']}.mp4")
    cortes.cortar_video(pasta, finais, video)
    lay = a.get("layout")
    antigos, _ = cortes.trechos_da_assinatura(lay["assinatura"]) if lay else (None, None)
    origem = lay and (lay.get("trechos_video") or antigos)  # cortador v1 não ajustava bordas
    if lay and antigos == json.loads(cortes.assinatura(trechos))["trechos"]:
        # mesmo conteúdo, só o cortador mudou: converte os tempos
        detectado = cortes.remapear_planos(lay["detectado"], origem, finais)
        plans = cortes.remapear_planos(lay["plans"], origem, finais)
        msg = "tempos convertidos para o corte novo (correções mantidas)"
    else:
        n = len(amostras["a"])
        detectado = channel_layout.planejar(
            *channel_layout.amostrar(video, finais, rostos, amostras["a"], amostras["sample_fps"]),
            sum(e - s for s, e in finais), cfg)
        salvar_amostras(pasta, amostras)
        plans = cortes.combinar_planos(lay["plans"], origem, detectado, finais, channel_layout.layout_key) if lay else detectado
        msg = (f"{len(amostras['a']) - n} amostras novas analisadas"
               + (", correções mantidas onde a fala continua" if lay else "") + f" | {channel_layout.describe(plans)}")
    print(f"[Layouts] {a['titulo']}: {msg}")
    novo = {"assinatura": cortes.assinatura(trechos), "trechos_video": finais, "detectado": detectado, "plans": plans}
    proj = carregar(pasta)  # relê: o usuário pode ter editado outros assuntos enquanto isso
    for b in proj["assuntos"]:
        if b["id"] == a["id"]:
            b["layout"] = novo
    proj["etapa"] = max(proj.get("etapa", 3), 3)
    salvar(pasta, proj)
    return novo, video


def layouts(pasta, ids, layout="layout_canal.json"):
    from scripts import channel_layout
    import cortes
    cfg = channel_layout.load_layout(layout)
    segmentos = cortes.carregar_transcricao(pasta)
    amostras = ler_amostras(pasta, cfg)
    alvos = [a for a in carregar(pasta)["assuntos"] if a["id"] in ids]
    for n, a in enumerate(alvos):
        progresso(100 * n / len(alvos), f"Layouts {n + 1}/{len(alvos)}: {a['titulo']}")
        analisar_layout(pasta, a, segmentos, cfg, amostras)
    progresso(100, f"Layouts analisados: {len(alvos)} assunto(s)")


# ---------------------------------------------------------------- 4. b-roll

def broll_valido(a):
    """B-roll do assunto, se foi feito para o corte atual (mesma assinatura do layout); senão None."""
    b, lay = a.get("broll"), a.get("layout")
    return b if b and lay and b.get("assinatura") == lay.get("assinatura") else None


def brolls(pasta, ids, modelo="sonnet", layout="layout_canal.json"):
    """O Claude propõe os b-rolls de cada assunto (momentos + busca) e as opções de mídia são buscadas.
    Nada é renderizado aqui: você confere na etapa 4 e a exportação aplica."""
    from scripts import channel_layout
    import broll
    import config
    import cortes
    cfg = config.carregar()
    if not cfg["pexels_chave"] and not cfg["pixabay_chave"] and not broll.arquivos(cfg["broll_pasta"]):
        raise SystemExit("Sem fonte de B-roll: em Configurações, informe a chave do Pexels ou do Pixabay e/ou uma pasta com suas mídias.")
    cfg_layout = channel_layout.load_layout(layout)
    segmentos = cortes.carregar_transcricao(pasta)
    amostras = ler_amostras(pasta, cfg_layout)
    alvos = [a for a in carregar(pasta)["assuntos"] if a["id"] in ids]
    total = 0
    for n, a in enumerate(alvos):
        progresso(100 * n / len(alvos), f"B-roll {n + 1}/{len(alvos)}: {a['titulo']}")
        lay, _ = layout_valido(pasta, a, segmentos)
        if not lay:  # os tempos do b-roll são os do corte: precisa do corte (etapa 3) em dia
            lay, _ = analisar_layout(pasta, a, segmentos, cfg_layout, amostras)
        legenda = cortes.legenda_dos_trechos(segmentos, lay["trechos_video"])["segments"]
        itens = broll.propor(legenda, sum(e - s for s, e in lay["trechos_video"]), cfg, modelo)
        print(f"[B-roll] {a['titulo']}: {len(itens)} momento(s), {sum(1 for i in itens if not i['opcoes'])} sem mídia")
        total += len(itens)
        proj = carregar(pasta)
        for b in proj["assuntos"]:
            if b["id"] == a["id"]:
                b["broll"] = {"assinatura": lay["assinatura"], "itens": itens}
        salvar(pasta, proj)
    progresso(100, f"{total} b-roll(s) propostos em {len(alvos)} assunto(s): confira antes de exportar")


# ---------------------------------------------------------------- 5. exportação

def _link(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def exportar(pasta, ids, formatos, layout="layout_canal.json"):
    from scripts import channel_layout, adjust_subtitles, burn_subtitles, export_premiere
    import cortes
    import legenda

    proj = carregar(pasta)
    escolhidos = [a for a in proj["assuntos"] if a["id"] in ids]
    if not escolhidos:
        raise SystemExit("Nenhum assunto selecionado para exportar.")

    # Área de trabalho limpa a cada exportação (a entrega fica em entrega/)
    for d in ("cuts", "final", "subs", "subs_ass", "burned_sub"):
        shutil.rmtree(os.path.join(pasta, d), ignore_errors=True)

    cfg = channel_layout.load_layout(layout)
    segmentos = cortes.carregar_transcricao(pasta)
    amostras = ler_amostras(pasta, cfg)
    os.makedirs(os.path.join(pasta, "final"), exist_ok=True)
    # 1) em sequência: análise de layout (modelo de rostos na placa) e corte de cada assunto
    prontos = []  # (assunto, base, layout, número)
    for i, a in enumerate(escolhidos):
        progresso(5 + 15 * i / len(escolhidos), f"Preparando {i + 1}/{len(escolhidos)}: {a['titulo']}")
        lay, video = layout_valido(pasta, a, segmentos)  # análise revisada na etapa 3: usa ela e o vídeo já cortado
        if not lay:  # assunto mudou depois da etapa 3 (ou nunca passou por ela): analisa agora, como a etapa 3 faria
            lay, video = analisar_layout(pasta, a, segmentos, cfg, amostras)
        n = cortes.numero(proj["assuntos"], a)  # número fixo do assunto na live ("003", "003-2"): exportar só um não renomeia os outros
        base, _ = cortes.cortar(pasta, n, a, segmentos, video_pronto=video, trechos_video=lay["trechos_video"])
        print(f"[Layout do canal] {base}: {channel_layout.describe(lay['plans'])}")
        prontos.append((a, base, lay, n))

    # 2) em paralelo: a montagem roda na placa, então vários cortes ao mesmo tempo aproveitam o encoder
    def em_paralelo(rotulo, p0, p1, fn, itens):
        feitos = 0
        with ThreadPoolExecutor(PARALELO) as ex:
            futuros = [ex.submit(fn, *it) for it in itens]
            for _ in as_completed(futuros):
                feitos += 1
                progresso(p0 + (p1 - p0) * feitos / len(itens), f"{rotulo} {feitos}/{len(itens)}")
        return [f.result() for f in futuros]

    em_paralelo("Montando", 20, 62, lambda a, base, lay, n: channel_layout.montar(
        os.path.join(pasta, "cuts", f"{base}_original_scale.mp4"), os.path.join(pasta, "final"), cfg, lay["plans"]), prontos)

    def por_broll(a, base, lay, n):  # por cima da montagem e por baixo da legenda; falha = corte sai sem b-roll
        import broll
        import config
        final = os.path.join(pasta, "final", f"{base}.mp4")
        itens = (broll_valido(a) or {}).get("itens")
        if a.get("broll") and itens is None:
            print(f"[B-roll] {a['titulo']}: o corte mudou depois do b-roll; exportado sem b-roll (gere de novo na etapa 4)")
        if not itens or not os.path.exists(final):
            return
        try:
            feitos = broll.aplicar(final, itens, pasta, config.carregar(), channel_layout.info_video(final)[0])
            print(f"[B-roll] {base}: {feitos} de {len(itens)} aplicado(s)")
        except subprocess.CalledProcessError as e:
            print(f"[B-roll] ERRO em {base}, exportado sem b-roll: {e}")
    if any(a.get("broll") for a, *_ in prontos):
        em_paralelo("B-roll", 62, 70, por_broll, prontos)

    sub_cfg = legenda.config_ass(legenda.carregar())  # estilo do canal, ajustado na etapa 4
    if "legenda" in formatos:
        progresso(70, "Gravando legendas...")
        adjust_subtitles.adjust(project_folder=pasta, **sub_cfg)
        os.makedirs(os.path.join(pasta, "burned_sub"), exist_ok=True)
        em_paralelo("Gravando legendas", 70, 85, lambda a, base, lay, n: burn_subtitles.burn_video_file(
            os.path.join(pasta, "final", f"{base}.mp4"), os.path.join(pasta, "subs_ass", f"{base}_processed.ass"),
            os.path.join(pasta, "burned_sub", f"{base}_subtitled.mp4"), legenda.FONTES), prontos)

    # 3) entrega: links e pacote editável (o fundo pré-renderizado, se pedido, também vai em paralelo)
    def entregar(a, base, lay, n):  # pasta com o título; arquivos só com o número (caminho curto)
        if not os.path.exists(os.path.join(pasta, "final", f"{base}.mp4")):
            print(f"[ERRO] O corte '{a['titulo']}' não foi montado; veja o log acima.")
            return {"id": a["id"], "erro": True}
        dest = os.path.join(pasta, "entrega", base)
        shutil.rmtree(dest, ignore_errors=True)  # nada de arquivo de uma exportação anterior deste assunto
        antiga = next((c.get("pasta") for c in (proj.get("exportacao") or {}).get("cortes", []) if c["id"] == a["id"]), None)
        if antiga and antiga.startswith("entrega/") and antiga != f"entrega/{base}":  # título mudou: sai a entrega com o nome velho
            shutil.rmtree(os.path.join(pasta, antiga), ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        arquivos = []
        if "legenda" in formatos:
            _link(os.path.join(pasta, "burned_sub", f"{base}_subtitled.mp4"), os.path.join(dest, f"{n}.mp4"))
            arquivos.append(f"{n}.mp4")
        if "sem_legenda" in formatos:
            _link(os.path.join(pasta, "final", f"{base}.mp4"), os.path.join(dest, f"{n}_sem_legenda.mp4"))
            arquivos.append(f"{n}_sem_legenda.mp4")
        if "editavel" in formatos:
            export_premiere.export_clip(pasta, base, cfg, export_premiere.load_sub_cfg(sub_cfg), n,
                                        desfoque_no_xml="fundo_renderizado" not in formatos)
            arquivos.append("editavel/")
        return {"id": a["id"], "pasta": f"entrega/{base}", "arquivos": arquivos}

    resultado = em_paralelo("Organizando a entrega", 85, 99, entregar, prontos)

    proj = carregar(pasta)
    ordem = [x["id"] for x in proj["assuntos"]]
    feitos = {r["id"] for r in resultado}
    antigos = [c for c in (proj.get("exportacao") or {}).get("cortes", []) if c["id"] in ordem and c["id"] not in feitos]
    proj["exportacao"] = {"formatos": formatos, "cortes": sorted(antigos + resultado, key=lambda c: ordem.index(c["id"]))}
    proj["etapa"] = 5
    salvar(pasta, proj)
    progresso(100, f"{len(resultado)} corte(s) exportado(s) em entrega/")


# ---------------------------------------------------------------- 6. postagem

def video_da_entrega(pasta, proj, aid):
    """Vídeo exportado do assunto para postar: o com legenda, senão o sem legenda. None se não foi exportado."""
    c = next((c for c in (proj.get("exportacao") or {}).get("cortes", []) if c["id"] == aid and not c.get("erro")), None)
    mp4 = sorted((f for f in (c or {}).get("arquivos", []) if f.endswith(".mp4")), key=lambda f: "_sem_legenda" in f)
    caminho = mp4 and os.path.join(pasta, c["pasta"], mp4[0])
    return caminho if caminho and os.path.exists(caminho) else None


def postar(pasta, ids, publico=True):
    """Posta cada assunto nas contas escolhidas (assunto["postagem"]: {titulo, descricao, contas}). O resultado de cada
    conta fica em postagem["feitas"][conta]: o que já foi postado não é postado de novo."""
    import config
    import postagem
    contas = {c["id"]: c for c in config.carregar()["contas"]}
    proj = carregar(pasta)
    fila = [(a, contas[c]) for a in proj["assuntos"] if a["id"] in ids
            for c in (a.get("postagem") or {}).get("contas", [])
            if c in contas and not ((a["postagem"].get("feitas") or {}).get(c) or {}).get("ok")]
    if not fila:
        raise SystemExit("Nada para postar: escolha as contas de cada corte (o que já foi postado não é repetido).")
    ok = 0
    for n, (a, conta) in enumerate(fila):
        rotulo = f"{postagem.NOMES[conta['plataforma']]} ({conta['nome']})"
        progresso(100 * n / len(fila), f"Postando {n + 1}/{len(fila)}: {a['titulo']} em {rotulo}")
        video = video_da_entrega(pasta, proj, a["id"])
        try:
            if not video:
                raise postagem.Erro("este corte ainda não foi exportado (etapa 5)")
            p = a["postagem"]
            feita = {"ok": True, "link": postagem.postar(conta, video, p.get("titulo") or a["titulo"], p.get("descricao", ""), publico)}
            ok += 1
        except postagem.Erro as e:
            feita = {"ok": False, "erro": str(e)}
        print(f"[Postagem] {a['titulo']} -> {rotulo}: {feita.get('link') or feita.get('erro') or 'postado'}")
        atual = carregar(pasta)  # grava a cada post: se a tarefa parar no meio, o que já foi não se repete
        for b in atual["assuntos"]:
            if b["id"] == a["id"] and b.get("postagem"):
                b["postagem"].setdefault("feitas", {})[conta["id"]] = dict(feita, data=time.strftime("%d/%m %H:%M"))
        atual["etapa"] = 6
        salvar(pasta, atual)
    progresso(100, f"{ok} de {len(fila)} postagem(ns) feitas" + ("" if ok == len(fila) else " (veja os erros em cada corte)"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tarefa", choices=["entrada", "assuntos", "layouts", "broll", "exportar", "postar"])
    ap.add_argument("--privado", action="store_true")
    ap.add_argument("--retranscrever", action="store_true")
    ap.add_argument("pasta")
    ap.add_argument("--url")
    ap.add_argument("--modelo", default="sonnet")
    ap.add_argument("--modo", choices=["novo", "mais"], default="novo")
    ap.add_argument("--min", type=float, default=0)
    ap.add_argument("--max", type=float, default=0)
    ap.add_argument("--ids", default="")
    ap.add_argument("--formatos", default="legenda")
    a = ap.parse_args()
    pasta = None if a.pasta == "-" else os.path.abspath(a.pasta)
    if a.tarefa == "entrada":
        entrada(pasta, a.url, a.retranscrever)
    elif a.tarefa == "assuntos":
        assuntos(pasta, a.modelo, a.modo, a.min, a.max)
    elif a.tarefa == "layouts":
        layouts(pasta, [i for i in a.ids.split(",") if i])
    elif a.tarefa == "broll":
        brolls(pasta, [i for i in a.ids.split(",") if i], a.modelo)
    elif a.tarefa == "postar":
        postar(pasta, [i for i in a.ids.split(",") if i], not a.privado)
    else:
        exportar(pasta, [i for i in a.ids.split(",") if i], [f for f in a.formatos.split(",") if f])
