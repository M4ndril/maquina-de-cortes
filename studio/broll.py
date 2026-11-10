"""
B-roll: imagens e vídeos de apoio por cima do corte já montado (antes da legenda, que fica por cima de tudo).

O Claude lê as falas do corte e propõe os momentos ({inicio, fim, busca, posicao}); cada momento ganha opções de mídia
(arquivo da sua pasta e/ou busca no Pexels/Pixabay) e você confere/troca na etapa de B-roll. Fica em assunto["broll"]:
  {"assinatura": <a do layout para o qual foi feito>, "itens": [{id, inicio, fim, texto, busca, posicao, motivo,
                                                                  opcoes: [{tipo, origem, url | arquivo, thumb}], escolha}]}
Tempos em segundos do corte. A mídia do Pexels só é baixada na exportação (VIRALS/<live>/broll/).
"""
import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request
import uuid

VIDEOS, IMAGENS = (".mp4", ".mov", ".webm", ".mkv", ".m4v"), (".jpg", ".jpeg", ".png", ".webp")
POSICOES = {"cheia": (0, 0, 1080, 1920), "cima": (0, 0, 1080, 960), "baixo": (0, 960, 1080, 960)}  # x, y, w, h na tela
DUR_MIN, DUR_MAX = 1.5, 8.0
SEM_BROLL_NO_INICIO = 2.0  # o gancho do corte é com quem fala na tela

PROMPT = """Você é editor de vídeos curtos (TikTok / Reels / Shorts). Abaixo estão as falas de UM corte, cada uma com o tempo
em segundos dentro do corte: `[início-fim] texto`. O corte dura {dur:.0f} s.

Escolha os momentos em que um B-ROLL (imagem ou vídeo de apoio mostrado por alguns segundos) deixa o corte mais claro ou
prende mais: quando a fala cita algo concreto e visual (um lugar, um objeto, um animal, um evento, uma atividade, dinheiro,
um gráfico...). Fala abstrata ou opinião sem nada para mostrar não leva b-roll.

REGRAS
- Cada b-roll dura de 2 a 6 segundos e começa junto com a fala que ele ilustra.
- Nenhum b-roll nos primeiros {gancho:.0f} segundos do corte.
- No máximo um a cada ~10 segundos de corte; eles não se sobrepõem. Se nada no corte for visual, devolva a lista vazia.
- busca: 2 a 4 palavras em INGLÊS para um banco de imagens (Pexels), concretas e genéricas ("horse riding mountains").
  Banco de imagens não tem pessoas famosas, marcas nem fatos específicos: descreva a cena, não o nome.
- posicao: "cheia" cobre a tela toda; "cima" ou "baixo" ocupa metade da tela e a outra metade continua mostrando quem fala.
  Prefira "cheia" quando o que importa é a imagem e "cima"/"baixo" quando a reação de quem fala importa.
- arquivo: se um arquivo da PASTA DO EDITOR (lista abaixo) combinar com o momento, copie o nome exato; senão deixe "".
- motivo: em poucas palavras, o que a imagem mostra.

PASTA DO EDITOR:
{arquivos}

FALAS DO CORTE:
{falas}"""
ESQUEMA = {"type": "object", "required": ["brolls"], "properties": {"brolls": {"type": "array", "items": {
    "type": "object", "required": ["inicio", "fim", "busca", "posicao"],
    "properties": {"inicio": {"type": "number"}, "fim": {"type": "number"}, "busca": {"type": "string"},
                   "posicao": {"enum": list(POSICOES)}, "arquivo": {"type": "string"}, "motivo": {"type": "string"}}}}}}


# ---------------------------------------------------------------- fontes de mídia

def arquivos(pasta, maximo=400):
    """Mídias da pasta do usuário (com subpastas), caminhos relativos com "/"."""
    out = []
    if pasta and os.path.isdir(pasta):
        for raiz, _, nomes in os.walk(pasta):
            for n in sorted(nomes):
                if n.lower().endswith(VIDEOS + IMAGENS):
                    out.append(os.path.relpath(os.path.join(raiz, n), pasta).replace("\\", "/"))
    return sorted(out)[:maximo]


def caminho_na_pasta(pasta, nome):
    """Arquivo da pasta de B-roll pelo caminho relativo; None se não existe ou aponta para fora dela."""
    if not pasta:
        return None
    alvo = os.path.realpath(os.path.join(pasta, nome))
    return alvo if alvo.startswith(os.path.realpath(pasta) + os.sep) and os.path.isfile(alvo) else None


def opcao_local(nome):
    return {"tipo": "video" if nome.lower().endswith(VIDEOS) else "foto", "origem": "pasta", "arquivo": nome}


def _pexels(caminho, chave, **params):
    req = urllib.request.Request(f"https://api.pexels.com/{caminho}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": chave, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def buscar_pexels(busca, chave, n=6):
    """Vídeos verticais primeiro; fotos completam a lista. -> opções (sem baixar nada). OSError se o Pexels falhar."""
    out = []
    for v in _pexels("videos/search", chave, query=busca, orientation="portrait", per_page=n).get("videos", []):
        mp4 = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width") and f.get("link")]
        if mp4:  # o menor arquivo com pelo menos 720 de largura (a tela tem 1080); senão o maior que houver
            bons = [f for f in mp4 if f["width"] >= 720]
            f = min(bons, key=lambda f: f["width"]) if bons else max(mp4, key=lambda f: f["width"])
            out.append({"tipo": "video", "origem": "pexels", "url": f["link"], "thumb": v.get("image", ""), "pagina": v.get("url", "")})
    if len(out) < n:
        for p in _pexels("v1/search", chave, query=busca, orientation="portrait", per_page=n - len(out)).get("photos", []):
            out.append({"tipo": "foto", "origem": "pexels", "url": p["src"]["large2x"], "thumb": p["src"]["medium"], "pagina": p.get("url", "")})
    return out


def _pixabay(caminho, chave, **params):
    req = urllib.request.Request(f"https://pixabay.com/api/{caminho}?{urllib.parse.urlencode(dict(params, key=chave))}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def buscar_pixabay(busca, chave, n=6):
    """Vídeos primeiro (o Pixabay não filtra vídeo por orientação: o recorte vertical é feito na montagem); fotos verticais completam."""
    out = []
    hits = _pixabay("videos/", chave, q=busca, per_page=50).get("hits", [])
    em_pe = lambda v: any(f.get("height", 0) > f.get("width", 0) for f in v.get("videos", {}).values())
    for v in sorted(hits, key=lambda v: not em_pe(v))[:n]:  # verticais primeiro: enchem a tela sem zoom
        # o menor arquivo com pelo menos 720 de largura (os tamanhos large/medium/small variam por vídeo); senão o maior
        fs = [f for f in v.get("videos", {}).values() if f.get("url") and f.get("width")]
        if fs:
            bons = [f for f in fs if f["width"] >= 720]
            f = min(bons, key=lambda f: f["width"]) if bons else max(fs, key=lambda f: f["width"])
            out.append({"tipo": "video", "origem": "pixabay", "url": f["url"], "pagina": v.get("pageURL", ""), "thumb": f.get("thumbnail", "")})
    if len(out) < n:
        for p in _pixabay("", chave, q=busca, image_type="photo", orientation="vertical", per_page=max(n - len(out), 3)).get("hits", [])[:n - len(out)]:
            out.append({"tipo": "foto", "origem": "pixabay", "url": p["largeImageURL"], "thumb": p["webformatURL"], "pagina": p.get("pageURL", "")})
    return out


def opcoes(busca, cfg, arquivo=""):
    """Opções de mídia para um momento: o arquivo que o Claude escolheu na pasta (se houver), depois a busca nos bancos com chave."""
    out = [opcao_local(arquivo)] if arquivo and caminho_na_pasta(cfg["broll_pasta"], arquivo) else []
    bancos = [(nome, f, cfg[nome + "_chave"]) for nome, f in (("pexels", buscar_pexels), ("pixabay", buscar_pixabay)) if cfg.get(nome + "_chave")]
    if busca.strip():
        for nome, buscar, chave in bancos:
            try:
                out += buscar(busca, chave, 6 if len(bancos) == 1 else 4)
            except OSError as e:
                print(f"[B-roll] busca no {nome.capitalize()} falhou ({busca}): {e}")
    return out


# ---------------------------------------------------------------- proposta do Claude

def validar(propostos, dur, nomes=()):
    """Momentos dentro do corte, com duração aceitável e sem sobreposição (vale o que vem primeiro)."""
    out = []
    for b in sorted(propostos, key=lambda b: float(b["inicio"])):
        ini = max(float(b["inicio"]), SEM_BROLL_NO_INICIO, out[-1]["fim"] if out else 0.0)
        fim = min(float(b["fim"]), ini + DUR_MAX, dur)
        if fim - ini < DUR_MIN or not str(b.get("busca", "")).strip():
            continue
        out.append({"id": uuid.uuid4().hex[:8], "inicio": round(ini, 2), "fim": round(fim, 2), "busca": b["busca"].strip(),
                    "posicao": b["posicao"] if b.get("posicao") in POSICOES else "cheia", "motivo": b.get("motivo", ""),
                    "arquivo": b.get("arquivo", "") if b.get("arquivo") in nomes else ""})
    return out


def texto_em(segmentos, ini, fim):
    """O que é dito no corte entre ini e fim (para a tela de revisão)."""
    return " ".join(w["word"].strip() for s in segmentos for w in s["words"] if w["end"] > ini and w["start"] < fim)


def propor(segmentos, dur, cfg, modelo="sonnet"):
    """segmentos: legenda do corte (tempos do corte). -> itens com as opções de mídia já buscadas."""
    import claude
    nomes = arquivos(cfg["broll_pasta"])
    falas = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segmentos)
    r = claude.perguntar(PROMPT.format(dur=dur, gancho=SEM_BROLL_NO_INICIO, falas=falas,
                                       arquivos="\n".join(nomes) or "(vazia)"), ESQUEMA, modelo)
    itens = validar(r["brolls"], dur, nomes)
    for it in itens:
        it["texto"] = texto_em(segmentos, it["inicio"], it["fim"])
        it["opcoes"], it["escolha"] = opcoes(it["busca"], cfg, it.pop("arquivo")), 0
    return itens


# ---------------------------------------------------------------- montagem

def midia(opcao, pasta_projeto, cfg):
    """Arquivo local da opção escolhida (baixa do banco na primeira vez). None se não der para obter."""
    if opcao["origem"] == "pasta":
        return caminho_na_pasta(cfg["broll_pasta"], opcao["arquivo"])
    destino = os.path.join(pasta_projeto, "broll", hashlib.sha1(opcao["url"].encode()).hexdigest()[:16]
                           + (".mp4" if opcao["tipo"] == "video" else ".jpg"))
    if not os.path.exists(destino):
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        try:
            req = urllib.request.Request(opcao["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(destino + ".part", "wb") as f:
                while bloco := r.read(1 << 20):
                    f.write(bloco)
            os.replace(destino + ".part", destino)
        except OSError as e:
            print(f"[B-roll] não consegui baixar {opcao['url']}: {e}")
            return None
    return destino


def filtro(itens, fps):
    """Filtro do ffmpeg: a entrada k+1 é a mídia do item k, já limitada à duração dele. -> (filtro, rótulo final)"""
    partes, atual = [], "0:v"
    for k, it in enumerate(itens, 1):
        x, y, w, h = POSICOES[it["posicao"]]
        encher = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        if it.get("encaixar"):  # mídia bem mais larga que o espaço: inteira no meio, com ela mesma desfocada no fundo
            encher = (f"split[e{k}][f{k}];[e{k}]{encher},boxblur=24:4,eq=brightness=-0.12[g{k}];"
                      f"[f{k}]scale={w}:{h}:force_original_aspect_ratio=decrease[h{k}];[g{k}][h{k}]overlay=(W-w)/2:(H-h)/2")
        partes.append(f"[{k}:v]{encher},setsar=1,fps={fps:.3f},"
                      f"format=yuv420p,setpts=PTS-STARTPTS+{it['inicio']:.3f}/TB[b{k}];"
                      f"[{atual}][b{k}]overlay={x}:{y}:enable='between(t,{it['inicio']:.3f},{it['fim']:.3f})':eof_action=pass[v{k}]")
        atual = f"v{k}"
    return ";".join(partes), atual


MAX_CORTE = 0.5  # encher o espaço pode cortar até metade da largura da mídia; mais que isso vira zoom demais


def _proporcao(arq):
    """largura/altura da mídia (0 se o ffprobe não souber: aí enche o espaço, como antes)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", arq], capture_output=True, text=True)
    try:
        w, h = map(int, r.stdout.strip().split(",")[:2])
        return w / h
    except ValueError:
        return 0


def aplicar(video, itens, pasta_projeto, cfg, fps):
    """Sobrepõe os b-rolls em 'video' (1080x1920, já montado), no lugar. -> quantos entraram."""
    prontos = []
    for it in itens:
        op = it["opcoes"][it["escolha"]] if 0 <= it.get("escolha", 0) < len(it.get("opcoes") or []) else None
        arq = op and midia(op, pasta_projeto, cfg)
        if arq:
            _, _, w, h = POSICOES[it["posicao"]]
            it = dict(it, encaixar=_proporcao(arq) * MAX_CORTE > w / h)
            prontos.append((it, op, arq))
        else:
            print(f"[B-roll] sem mídia para o momento {it['inicio']:.1f}s ({it['busca']}): fica sem b-roll")
    if not prontos:
        return 0
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video]
    for it, op, arq in prontos:
        d = f"{it['fim'] - it['inicio']:.3f}"
        cmd += (["-stream_loop", "-1", "-t", d, "-i", arq] if op["tipo"] == "video" else ["-loop", "1", "-t", d, "-i", arq])
    grafo, final = filtro([it for it, _, _ in prontos], fps)
    tmp = video + ".broll.mp4"
    subprocess.run(cmd + ["-filter_complex", grafo, "-map", f"[{final}]", "-map", "0:a?", "-c:v", "h264_nvenc", "-b:v", "8M",
                          "-c:a", "copy", tmp], check=True)
    os.replace(tmp, video)
    return len(prontos)


def selftest():
    # validação: dentro do corte, depois do gancho, duração limitada, sem sobrepor, arquivo só se existe na pasta
    v = validar([{"inicio": 0.5, "fim": 4.0, "busca": "horse", "posicao": "cima", "arquivo": "x.mp4"},
                 {"inicio": 3.0, "fim": 30.0, "busca": "mountain", "posicao": "torta", "arquivo": "a.jpg"},
                 {"inicio": 12.5, "fim": 13.0, "busca": "curto", "posicao": "cheia"},
                 {"inicio": 58.0, "fim": 70.0, "busca": "fim", "posicao": "baixo"},
                 {"inicio": 20.0, "fim": 24.0, "busca": " ", "posicao": "cheia"}], 60.0, ["a.jpg"])
    assert [(b["inicio"], b["fim"], b["posicao"], b["arquivo"]) for b in v] == [
        (2.0, 4.0, "cima", ""), (4.0, 12.0, "cheia", "a.jpg"), (58.0, 60.0, "baixo", "")], v
    segs = [{"words": [{"word": "um", "start": 0.0, "end": 1.0}, {"word": "dois", "start": 1.0, "end": 2.0}, {"word": "três", "start": 2.0, "end": 3.0}]}]
    assert texto_em(segs, 1.2, 2.5) == "dois três"
    assert opcao_local("Praia/Mar.MOV") == {"tipo": "video", "origem": "pasta", "arquivo": "Praia/Mar.MOV"} and opcao_local("a.png")["tipo"] == "foto"
    g, final = filtro([{"inicio": 2.0, "fim": 5.0, "posicao": "cima"}, {"inicio": 9.0, "fim": 12.0, "posicao": "cheia"}], 30)
    assert "boxblur" in filtro([{"inicio": 1.0, "fim": 2.0, "posicao": "cheia", "encaixar": True}], 30)[0] and "boxblur" not in g
    assert final == "v2" and "[0:v][b1]overlay=0:0:enable='between(t,2.000,5.000)'" in g and "[v1][b2]overlay=0:0" in g and "scale=1080:960" in g, g
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "sub"))
        for n in ("b.jpg", "sub/a.mp4", "notas.txt"):
            open(os.path.join(d, n), "w").close()
        assert arquivos(d) == ["b.jpg", "sub/a.mp4"] and arquivos("") == []
        assert caminho_na_pasta(d, "sub/a.mp4") and not caminho_na_pasta(d, "../fora.mp4") and not caminho_na_pasta(d, "nada.mp4")
    print("selftest ok")


if __name__ == "__main__":
    selftest()
