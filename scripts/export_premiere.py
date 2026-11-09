"""
Exporta os cortes do layout do canal para o Premiere Pro (XML FCP7 / xmeml), com cada camada editável.

Para cada corte gera <projeto>/entrega/<nome>/ com <nome>.mp4 (pronto, com legenda) e editavel/:
  <nome>.xml   sequência 1080x1920: uma trilha por camada (recorte via efeito Cortar + Movimento),
               cortada nas trocas de layout / de quem fala
  <nome>_original.mp4 corte bruto (hard link); o fundo desfocado é ele mesmo com efeito Desfoque gaussiano no XML,
               ou <nome>_fundo.mp4 já renderizado (opção, para editores que não leem efeitos do XML)
  <nome>.srt   legenda em blocos de palavras; no Premiere: Importar e arrastar para a sequência = trilha de legenda
               editável (o XML não tem como carregar trilha de legenda)

As camadas vêm de channel_layout.layers(), as mesmas usadas na renderização, então o Premiere mostra o mesmo vídeo.

Uso pelo Estúdio (studio/tarefas.py); avulso só o teste: python scripts/export_premiere.py selftest
"""
import json
import os
import re
import shutil
import sys
import uuid
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import channel_layout as cl                # noqa: E402
from scripts.adjust_subtitles import sem_pontuacao      # noqa: E402

SEQ_W, SEQ_H = cl.OUT_W, cl.OUT_H

LEIA_ME = """Corte: {base}

../{base}.mp4  -> versão pronta (com legenda)

Para editar no Premiere Pro (mantenha todos os arquivos desta pasta juntos):
  1. Arquivo > Importar > {base}.xml  -> cria a sequência vertical montada, uma camada por trilha
     (se o Premiere pedir para localizar a mídia, aponte para esta pasta; ele encontra o resto sozinho)
  2. Arquivo > Importar > {base}.srt e arraste para a sequência -> trilha de legenda editável
  3. O estilo da legenda (fonte, tamanho, posição) se define no painel Propriedades / Essential Graphics

Arquivos:
  {base}.xml           sequência (camadas com recorte e posição editáveis)
  {base}.srt           legenda
  {base}_original.mp4  corte bruto em 1920x1080 (fonte de todas as camadas e do áudio)
{fundo}"""
FUNDO_RENDERIZADO = "  {base}_fundo.mp4     fundo desfocado já renderizado (só nos trechos com câmeras laterais / tela cheia)\n"
FUNDO_NO_XML = """
O fundo desfocado é o próprio corte com o efeito Desfoque gaussiano (trilha V1). Em editores que não
leem efeitos do XML (ex.: DaVinci Resolve) ele chega sem desfoque: aplique um desfoque nessa trilha ou
exporte de novo marcando "pré-renderizar o fundo desfocado".
"""


def uid():
    return uuid.uuid4().hex[:12]


def file_xml(fid, path, fps, n_frames, w, h, seen):
    """Bloco <file> completo na primeira vez, referência nas seguintes."""
    if fid in seen:
        return f'<file id="{fid}"/>'
    seen.add(fid)
    # Só o nome do arquivo: a mídia fica ao lado do XML, então a pasta pode ir para outro PC
    nome = escape(os.path.basename(path))
    return (f'<file id="{fid}"><name>{nome}</name><pathurl>{nome}</pathurl>'
            f'<rate><timebase>{int(round(fps))}</timebase><ntsc>FALSE</ntsc></rate><duration>{n_frames}</duration>'
            f'<media><video><samplecharacteristics><width>{int(w)}</width><height>{int(h)}</height><alpha>straight</alpha>'
            f'</samplecharacteristics></video><audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>'
            f'</samplecharacteristics><channelcount>2</channelcount></audio></media></file>')


def link_or_copy(src, dst):
    """Hard link (não ocupa espaço extra; vira arquivo normal ao copiar/zipar a pasta); cópia se não der."""
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def motion_and_crop(src, dst, src_w, src_h, crop=True, cobrir=False):
    """Efeitos Cortar (em % do original) + Movimento (escala %, centro normalizado) que levam o retângulo src
    do vídeo original ao retângulo dst da sequência vertical. cobrir: amplia até cobrir dst inteiro (fundo),
    com 10% a mais para as bordas do desfoque ficarem fora da tela."""
    sx, sy, sw, sh = src
    dx, dy, dw, dh = dst
    s = max(dw / sw, dh / sh) * 1.1 if cobrir else dw / sw
    # O Cortar não move a imagem: o centro do quadro original precisa ficar onde o centro do recorte cai na tela.
    # O Premiere lê o Center do XML como deslocamento do centro da sequência em unidades do vídeo de ORIGEM
    # (horiz x largura da origem, vert x altura da origem) - medido num projeto importado.
    frame_cx = dx + dw / 2 - (sx + sw / 2 - src_w / 2) * s
    frame_cy = dy + dh / 2 - (sy + sh / 2 - src_h / 2) * s
    out = ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
           "<effecttype>motion</effecttype><mediatype>video</mediatype>"
           f'<parameter authoringApp="PremierePro"><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{s * 100:.4f}</value></parameter>'
           f'<parameter authoringApp="PremierePro"><parameterid>center</parameterid><name>Center</name><value>'
           f"<horiz>{(frame_cx - SEQ_W / 2) / src_w:.6f}</horiz><vert>{(frame_cy - SEQ_H / 2) / src_h:.6f}</vert></value></parameter>"
           "</effect></filter>")
    if crop:
        pct = {"left": sx / src_w, "right": (src_w - sx - sw) / src_w, "top": sy / src_h, "bottom": (src_h - sy - sh) / src_h}
        params = "".join(f'<parameter authoringApp="PremierePro"><parameterid>{k}</parameterid><name>{k}</name>'
                         f"<valuemin>0</valuemin><valuemax>100</valuemax><value>{v * 100:.4f}</value></parameter>"
                         for k, v in pct.items())
        out += ("<filter><effect><name>Crop</name><effectid>crop</effectid><effectcategory>motion</effectcategory>"
                f"<effecttype>motion</effecttype><mediatype>video</mediatype>{params}</effect></filter>")
    return out


DESFOQUE_XML = ("<filter><effect><name>Gaussian Blur</name><effectid>Gaussian Blur</effectid><effectcategory>Blur</effectcategory>"
                "<effecttype>filter</effecttype><mediatype>video</mediatype>"
                "<parameter><parameterid>radius</parameterid><name>Radius</name><valuemin>0</valuemin><valuemax>100</valuemax>"
                "<value>30</value></parameter></effect></filter>")


def clipitem(name, file_block, fps, n_frames, start, end, filters):
    tb = int(round(fps))
    return (f'<clipitem id="clipitem-{uid()}"><name>{escape(name)}</name><enabled>TRUE</enabled><duration>{n_frames}</duration>'
            f"<rate><timebase>{tb}</timebase><ntsc>FALSE</ntsc></rate><start>{start}</start><end>{end}</end>"
            f"<in>{start}</in><out>{end}</out>{file_block}{filters}</clipitem>")


def export_clip(project, base, cfg, sub_cfg, nome, desfoque_no_xml=True):
    """Pacote editável em entrega/<base>/editavel, com os arquivos chamados só pelo número do corte (nome = "009"):
    o título já está no nome da pasta e repeti-lo estourava o limite de 260 caracteres de caminho.
    desfoque_no_xml: o fundo é o próprio corte com efeito de desfoque gaussiano no XML (testado no Premiere);
    False = renderiza _fundo.mp4 (para editores que não leem efeitos do XML)."""
    cut = os.path.join(project, "cuts", f"{base}_original_scale.mp4")
    with open(os.path.join(project, "final", f"{base}_layout.json"), encoding="utf-8") as f:
        plans = json.load(f)
    import cv2
    cap = cv2.VideoCapture(cut)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_w, src_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    pack = os.path.join(project, "entrega", base)
    out_dir = os.path.join(pack, "editavel")
    os.makedirs(out_dir, exist_ok=True)
    original = os.path.join(out_dir, f"{nome}_original.mp4")
    link_or_copy(cut, original)
    cut = original
    with open(os.path.join(out_dir, "LEIA-ME.txt"), "w", encoding="utf-8") as f:
        f.write(LEIA_ME.format(base=nome, fundo=(FUNDO_NO_XML if desfoque_no_xml else FUNDO_RENDERIZADO.format(base=nome))))
    fundo = os.path.join(out_dir, f"{nome}_fundo.mp4")
    if os.path.exists(fundo):
        os.remove(fundo)  # de uma exportação anterior: não serve para este XML
    if not desfoque_no_xml and any(p["tipo"] != "quadros" for p in plans):
        print(f"[Premiere] {base}: renderizando fundo desfocado...")
        cl.render(cut, fundo, fps, n_frames, plans, cfg, only_fundo=True)

    seen, cut_id, fundo_id = set(), f"file-corte-{uid()}", f"file-fundo-{uid()}"
    tracks = {}  # índice da trilha -> clipitems
    for i, plan in enumerate(plans):
        f0 = round(plan["t0"] * fps)
        f1 = n_frames if i == len(plans) - 1 else round(plans[i + 1]["t0"] * fps)
        ls = cl.layers(plan, cfg)
        offset = 0 if any(l.get("fundo") for l in ls) else 1  # fundo sempre na V1; demais camadas em ordem acima dele
        for k, l in enumerate(ls):
            track = k + offset
            if l.get("fundo") and desfoque_no_xml:
                fb = file_xml(cut_id, cut, fps, n_frames, src_w, src_h, seen)
                tracks.setdefault(track, []).append(clipitem(l["nome"], fb, fps, n_frames, f0, f1,
                                                             DESFOQUE_XML + motion_and_crop(l["src"], l["dst"], src_w, src_h, cobrir=True)))
                continue
            if l.get("fundo"):
                fb = file_xml(fundo_id, fundo, fps, n_frames, SEQ_W, SEQ_H, seen)
                tracks.setdefault(track, []).append(clipitem(l["nome"], fb, fps, n_frames, f0, f1,
                                                             motion_and_crop((0, 0, SEQ_W, SEQ_H), (0, 0, SEQ_W, SEQ_H), SEQ_W, SEQ_H, crop=False)))
                continue
            trocas = l.get("trocas") or [(0.0, l["src"][0])]
            for j, (t, x) in enumerate(trocas):  # um clipe por troca de quem fala
                a = f0 + round(t * fps)
                b = f0 + round(trocas[j + 1][0] * fps) if j + 1 < len(trocas) else f1
                if b <= a:
                    continue
                src = (x,) + tuple(l["src"][1:])
                fb = file_xml(cut_id, cut, fps, n_frames, src_w, src_h, seen)
                tracks.setdefault(track, []).append(clipitem(l["nome"], fb, fps, n_frames, a, b,
                                                             motion_and_crop(src, l["dst"], src_w, src_h)))

    # Legenda: o XML do Premiere não carrega trilha de legendas; vai como .srt (Importar -> vira trilha de legenda editável)
    subs_json = os.path.join(project, "subs", f"{base}_processed.json")
    if os.path.exists(subs_json):
        with open(subs_json, encoding="utf-8") as f:
            data = json.load(f)
        with open(os.path.join(out_dir, f"{nome}.srt"), "w", encoding="utf-8") as f:
            f.write(word_block_srt(data.get("segments", data) if isinstance(data, dict) else data, **sub_cfg))

    video_tracks = "".join(f"<track>{''.join(tracks[t])}</track>" for t in sorted(tracks))
    audio = clipitem(base, file_xml(cut_id, cut, fps, n_frames, src_w, src_h, seen), fps, n_frames, 0, n_frames,
                     "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>")
    tb = int(round(fps))
    seq_id = str(uuid.uuid4())
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
<sequence id="{seq_id}">
    <uuid>{seq_id}</uuid>
    <name>{escape(base)}</name>
    <duration>{n_frames}</duration>
    <rate><timebase>{tb}</timebase><ntsc>FALSE</ntsc></rate>
    <media>
        <video>
            <format><samplecharacteristics><rate><timebase>{tb}</timebase><ntsc>FALSE</ntsc></rate><width>{SEQ_W}</width><height>{SEQ_H}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
            {video_tracks}
        </video>
        <audio>
            <numOutputChannels>2</numOutputChannels>
            <format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>
            <track>{audio}</track>
        </audio>
    </media>
</sequence>
</xmeml>"""
    xml_path = os.path.join(out_dir, f"{nome}.xml")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"[Premiere] {xml_path} ({len(tracks)} trilhas de vídeo)")
    return xml_path


def _srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def word_block_srt(segments, words_per_block=3, uppercase=False, remove_punctuation=True):
    """SRT em blocos de N palavras com os tempos de cada palavra (mesmo ritmo da legenda renderizada)."""
    blocks = []
    for s in segments:
        words = [w for w in s.get("words", []) if "start" in w and "end" in w]
        if not words:  # sem tempo por palavra: a frase inteira
            blocks.append((s.get("start", 0), s.get("end", 0), s.get("text", "").strip()))
            continue
        for i in range(0, len(words), words_per_block):
            b = words[i:i + words_per_block]
            blocks.append((b[0]["start"], b[-1]["end"], " ".join(w["word"].strip() for w in b)))
    out = []
    for n, (a, b, text) in enumerate(blocks, 1):
        if remove_punctuation:
            text = sem_pontuacao(text)
        if uppercase:
            text = text.upper()
        out.append(f"{n}\n{_srt_time(a)} --> {_srt_time(max(b, a + 0.05))}\n{text}\n")
    return "\n".join(out)


def load_sub_cfg(raw):
    """Do estilo da legenda, o que vale para o .srt: palavras por bloco, maiúsculas, pontuação."""
    return {"words_per_block": int(raw.get("words_per_block", 3)), "uppercase": bool(raw.get("uppercase", False)),
            "remove_punctuation": bool(raw.get("remove_punctuation", True))}


def selftest():
    assert sem_pontuacao("levaria a 48,6%, 1.000 votos. Às 10:30!") == "levaria a 48,6% 1.000 votos Às 10:30"
    # Recorte do quadro da direita (x 1286, y 40, 624x760) -> área de cima (0,0,1080,1314)
    m = motion_and_crop((1286, 40, 624, 760), (0, 0, 1080, 1314), 1920, 1080)
    s = 1080 / 624
    assert f"<value>{s * 100:.4f}</value>" in m
    # centro do recorte (1598, 420) deve cair no centro da área (540, 657): centro do quadro em 540-(1598-960)*s, 657-(420-540)*s.
    # Premiere (medido): pixel = centro da sequência + horiz * largura da origem / vert * altura da origem
    cx, cy = 540 - (1598 - 960) * s, 657 - (420 - 540) * s
    h = float(re.search(r"<horiz>([-\d.]+)</horiz>", m).group(1))
    v = float(re.search(r"<vert>([-\d.]+)</vert>", m).group(1))
    assert abs(540 + h * 1920 - cx) < 0.01 and abs(960 + v * 1080 - cy) < 0.01, m
    assert "<parameterid>left</parameterid><name>left</name><valuemin>0</valuemin><valuemax>100</valuemax><value>66.9792</value>" in m
    # Vídeo inteiro, sem corte, na base: escala 56.25%, centro horizontal 0
    m = motion_and_crop((0, 0, 1920, 1080), (0, 1314, 1080, 606), 1920, 1080)
    assert "<value>56.2500</value>" in m and "<horiz>0.000000</horiz>" in m
    # SRT em blocos de palavras
    srt = word_block_srt([{"words": [{"word": "Olá,", "start": 0.0, "end": 0.4}, {"word": "tudo", "start": 0.5, "end": 0.8},
                                     {"word": "bem?", "start": 0.9, "end": 1.2}, {"word": "Sim", "start": 1.5, "end": 1.9}]}], 3)
    assert srt == "1\n00:00:00,000 --> 00:00:01,200\nOlá tudo bem\n\n2\n00:00:01,500 --> 00:00:01,900\nSim\n", srt
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["selftest"]:
        selftest()
    else:
        print(__doc__)
