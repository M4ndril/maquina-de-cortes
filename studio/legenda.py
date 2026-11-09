"""
Estilo da legenda do canal (legenda_canal.json, vale para todas as lives) e prévia num quadro real do corte.
O estilo fica em termos simples (cores #RRGGBB, tamanhos); config_ass() traduz para o adjust_subtitles.
"""
import functools
import glob
import json
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQUIVO = os.path.join(ROOT, "legenda_canal.json")
FONTES = os.path.join(ROOT, "fontes")  # .ttf/.otf extras: entram na lista e no FFmpeg (fontsdir)

PADRAO = {
    "fonte": "Arial", "tamanho": 30, "tamanho_destaque": 35, "cor": "#FFFFFF", "cor_destaque": "#00FF00",
    "contorno": 0.0, "cor_contorno": "#000000", "sombra": 2.0, "negrito": False, "maiusculas": False,
    "palavras": 3, "modo": "highlight", "sem_pontuacao": True, "zoom": True, "pausa": 1.5,
    "margem": 40,  # px de cada lado no vídeo final (1080 de largura): a frase quebra a linha antes de encostar na borda
}
MODOS = ("highlight", "colorir", "no_highlight", "palavra_por_palavra")


def limpar(estilo):
    """Só as chaves conhecidas, no tipo do padrão; o que vier errado volta ao padrão."""
    out = dict(PADRAO)
    for k, v in (estilo or {}).items():
        if k not in PADRAO:
            continue
        try:
            out[k] = type(PADRAO[k])(v)
        except (TypeError, ValueError):
            continue
    for k in ("cor", "cor_destaque", "cor_contorno"):
        c = out[k]
        if not (len(c) == 7 and c[0] == "#" and all(x in "0123456789abcdefABCDEF" for x in c[1:])):
            out[k] = PADRAO[k]
    if out["modo"] not in MODOS:
        out["modo"] = PADRAO["modo"]
    out["palavras"] = min(max(out["palavras"], 1), 8)
    out["tamanho"] = min(max(out["tamanho"], 8), 120)
    out["tamanho_destaque"] = min(max(out["tamanho_destaque"], 8), 140)
    out["pausa"] = min(max(out["pausa"], 0.0), 5.0)
    out["margem"] = min(max(out["margem"], 0), 400)
    return out


def carregar():
    if os.path.exists(ARQUIVO):
        with open(ARQUIVO, encoding="utf-8") as f:
            return limpar(json.load(f))
    return dict(PADRAO)


def salvar(estilo):
    estilo = limpar(estilo)
    with open(ARQUIVO + ".tmp", "w", encoding="utf-8") as f:
        json.dump(estilo, f, ensure_ascii=False, indent=1)
    os.replace(ARQUIVO + ".tmp", ARQUIVO)
    return estilo


def cor_ass(hexa):
    """#RRGGBB -> &H00BBGGRR& (ASS guarda azul-verde-vermelho; 00 = opaco)."""
    return f"&H00{hexa[5:7]}{hexa[3:5]}{hexa[1:3]}&".upper()


def config_ass(estilo):
    """Estilo -> argumentos do adjust_subtitles.adjust (e do .srt do pacote editável)."""
    e = limpar(estilo)
    return {
        "font": e["fonte"], "base_size": e["tamanho"], "base_color": cor_ass(e["cor"]),
        "highlight_size": e["tamanho_destaque"] if e["zoom"] else e["tamanho"], "highlight_color": cor_ass(e["cor_destaque"]),
        # pausa: silêncio de até tantos segundos não apaga a legenda (respiração não faz a frase sumir e voltar)
        "words_per_block": e["palavras"], "gap_limit": e["pausa"], "mode": e["modo"],
        "margin": round(e["margem"] * 360 / 1080),  # o .ass trabalha numa tela de 360 de largura
        "vertical_position": 210, "alignment": 2,  # posição padrão; trechos laterais seguem o layout
        "bold": -1 if e["negrito"] else 0, "italic": 0, "underline": 0, "strikeout": 0,
        "border_style": 1, "outline_thickness": e["contorno"], "outline_color": cor_ass(e["cor_contorno"]),
        "shadow_size": e["sombra"], "shadow_color": "&H00000000&",
        "uppercase": e["maiusculas"], "remove_punctuation": e["sem_pontuacao"],
    }


ESTILOS_COMUNS = {"Regular", "Normal", "Bold", "Italic", "Bold Italic", "Book", "Roman"}


def nome_da_fonte(arq):
    """Nome que aparece na lista e que o libass procura: a família, ou "Família Peso" para pesos extras
    ("Montserrat Black"): o libass acha a fonte pelo nome completo, então cada peso vira uma opção."""
    from PIL import ImageFont
    familia, estilo = ImageFont.truetype(arq, 10).getname()
    return familia if estilo in ESTILOS_COMUNS else f"{familia} {estilo}"


@functools.lru_cache
def _fontes_windows():
    from PIL import ImageFont
    nomes = set()
    for arq in glob.glob(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "*.[ot]t[fc]")):
        try:
            nomes.add(ImageFont.truetype(arq, 10).getname()[0])
        except OSError:
            pass
    return nomes


def fontes():
    """Fontes do Windows + as da pasta fontes/ (enviadas ou baixadas do Google Fonts; relidas a cada vez)."""
    os.makedirs(FONTES, exist_ok=True)
    nomes = set(_fontes_windows())
    for arq in glob.glob(os.path.join(FONTES, "*.[ot]t[fc]")):
        try:
            nomes.add(nome_da_fonte(arq))
        except OSError:
            pass
    return sorted(nomes, key=str.lower)


def guardar_fonte(nome_arquivo, dados):
    """Arquivo de fonte enviado pelo usuário -> fontes/. Devolve o nome da fonte (ValueError se não for fonte)."""
    import tempfile
    ext = os.path.splitext(nome_arquivo)[1].lower()
    if ext not in (".ttf", ".otf"):
        raise ValueError("Envie um arquivo .ttf ou .otf")
    with tempfile.TemporaryDirectory() as tmp:
        arq = os.path.join(tmp, "f" + ext)
        with open(arq, "wb") as f:
            f.write(dados)
        try:
            nome = nome_da_fonte(arq)
        except OSError:
            raise ValueError("Esse arquivo não é uma fonte válida")
    seguro = "".join(c for c in nome if c.isalnum() or c in " -_").strip().replace(" ", "_") or "fonte"
    with open(os.path.join(FONTES, seguro + ext), "wb") as f:
        f.write(dados)
    return nome


# ---- Google Fonts: lista de famílias (metadados públicos do site) e download dos .ttf de cada peso (sem chave de API)

@functools.lru_cache
def _catalogo_google():
    import urllib.request
    with urllib.request.urlopen("https://fonts.google.com/metadata/fonts", timeout=30) as r:
        texto = r.read().decode("utf-8")
    dados = json.loads(texto[texto.index("{"):])  # a resposta começa com )]}' antes do JSON
    return {f["family"]: f for f in dados["familyMetadataList"]}


def buscar_google(texto, limite=20):
    """Famílias do Google Fonts que contêm o texto, as mais usadas primeiro."""
    texto = texto.strip().lower()
    achadas = [f for nome, f in _catalogo_google().items() if texto in nome.lower()]
    achadas.sort(key=lambda f: (not f["family"].lower().startswith(texto), f.get("popularity", 1e9)))
    return [{"familia": f["family"], "pesos": sorted(int(k) for k in f.get("fonts", {}) if k.isdigit())} for f in achadas[:limite]]


def baixar_google(familia):
    """Baixa todos os pesos (sem itálico) da família para fontes/. Devolve os nomes que entraram na lista."""
    import re
    import urllib.parse
    import urllib.request
    info = _catalogo_google().get(familia)
    if not info:
        raise ValueError(f"Fonte \"{familia}\" não encontrada no Google Fonts")
    pesos = sorted(int(k) for k in info.get("fonts", {}) if k.isdigit()) or [400]
    url = "https://fonts.googleapis.com/css2?family=" + urllib.parse.quote(familia) + ":wght@" + ";".join(map(str, pesos))
    with urllib.request.urlopen(url, timeout=30) as r:  # pedido sem navegador: o Google responde com .ttf
        css = r.read().decode("utf-8")
    nomes = []
    for peso, link in re.findall(r"font-weight: (\d+);\s*src: url\(([^)]+\.ttf)\)", css):
        arq = os.path.join(FONTES, f"{familia.replace(' ', '_')}-{peso}.ttf")
        urllib.request.urlretrieve(link, arq)
        nomes.append(nome_da_fonte(arq))
    if not nomes:
        raise ValueError(f"O Google Fonts não devolveu arquivos para \"{familia}\"")
    return nomes


def previa(pasta, assunto, t, estilo, largura=360):
    """JPEG da montagem vertical do assunto no instante t do corte, com a legenda queimada como na exportação."""
    import cortes
    from scripts import adjust_subtitles, channel_layout as cl
    from scripts.burn_subtitles import filtro_legenda
    lay = assunto.get("layout")
    video = os.path.join(pasta, "layouts", f"{assunto['id']}.mp4")
    if not lay or not os.path.exists(video):
        raise LookupError("Analise os layouts deste assunto (etapa 3) para ver a prévia")
    cfg = cl.load_layout()
    with tempfile.TemporaryDirectory() as tmp:  # mesmo caminho da exportação: subs -> adjust_subtitles -> .ass
        os.makedirs(os.path.join(tmp, "subs"))
        os.makedirs(os.path.join(tmp, "final"))
        with open(os.path.join(tmp, "subs", "p_processed.json"), "w", encoding="utf-8") as f:
            json.dump(cortes.legenda_dos_trechos(cortes.carregar_transcricao(pasta), lay["trechos_video"]), f)
        with open(os.path.join(tmp, "final", "p_timeline.json"), "w") as f:
            json.dump(cl.timeline_legenda(lay["plans"], cfg), f)
        adjust_subtitles.adjust(project_folder=tmp, **config_ass(estilo))
        ass = os.path.join(tmp, "subs_ass", "p_processed.ass")
        # a montagem zera o relógio do quadro; a legenda precisa do instante t do corte
        filtro = (cl.build_filter(cl.plano_em(lay["plans"], t), cfg)
                  + f";[v]setpts=PTS+{t:.3f}/TB,{filtro_legenda(ass, FONTES)},scale={largura}:-2[s]")
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video, "-frames:v", "1",
                            "-filter_complex", filtro, "-map", "[s]", "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                           capture_output=True, check=True)
        return r.stdout


if __name__ == "__main__":
    assert cor_ass("#00ff80") == "&H0080FF00&"
    e = limpar({"tamanho": "40", "cor": "verde", "modo": "x", "palavras": 99, "lixo": 1})
    assert e["tamanho"] == 40 and e["cor"] == "#FFFFFF" and e["modo"] == "highlight" and e["palavras"] == 8 and "lixo" not in e
    assert config_ass(dict(PADRAO, zoom=False))["highlight_size"] == PADRAO["tamanho"] and config_ass(PADRAO)["gap_limit"] == 1.5
    assert config_ass(PADRAO)["base_color"] == "&H00FFFFFF&" and config_ass(dict(PADRAO, margem=90))["margin"] == 30 and config_ass(PADRAO)["highlight_color"] == "&H0000FF00&"
    print("selftest ok")
