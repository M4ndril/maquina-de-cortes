"""
Layout vertical do canal.

O layout da live muda durante o programa; cada trecho do corte é montado conforme o que está na tela:
  - QUADROS (2 ou 3 pessoas lado a lado): zoom em quem fala em cima, vídeo inteiro embaixo
    (o vídeo inteiro é sobreposto por último, então o zoom nunca o cobre)
  - LATERAL (coluna de câmeras pequenas + área grande): área grande em cima em destaque
    (câmera = zoom no rosto; apresentação/vídeo = conteúdo inteiro sobre fundo desfocado),
    câmeras pequenas lado a lado embaixo
  - PESSOA (tela cheia com gente: podcast, entrevista, vlog, palestra, qualquer vídeo fora da geometria do canal):
    recorte vertical na altura toda, centrado em quem fala; acompanha troca de quem fala, corte de cena e a pessoa
    saindo do meio (ver seguir_rostos)
  - OUTRO (tela cheia sem rosto: vinheta, paisagem, gráfico): quadro inteiro sobre fundo desfocado

Detecção: as bordas dos quadros do programa de transmissão são linhas retas longas
(picos no perfil de bordas verticais/horizontais). Quem fala: movimento da boca (InsightFace).
Parâmetros em layout_canal.json.

Uso avulso:
  python scripts/channel_layout.py preview <video.mp4> <segundos>   -> mostra o layout detectado e gera preview.jpg
  python scripts/channel_layout.py selftest
"""
import json
import os
import subprocess
import sys
from collections import Counter

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_W, OUT_H = 1080, 1920
DEFAULT_LAYOUT = os.path.join(ROOT, "layout_canal.json")
EDGE_T = 40          # gradiente mínimo para contar como borda
PEAK_T = 0.6         # fração mínima da linha/coluna com borda para ser borda de quadro
GRANDE_GAP = 40     # a área grande começa logo depois da coluna de câmeras (17 px); procurar mais longe pegava a
                     # borda do vídeo/site compartilhado (borda real fraca: escuro sobre escuro) e cortava a esquerda
SO_TOPO_T = 0.8      # câmera da coluna reconhecida só pela borda de cima (sem o par): borda quase contínua
ENCODER = ("h264_nvenc", "fast")  # placa NVIDIA (o resto do Studio também usa NVENC)
BIG_FACE_H = 120     # rosto acima disso na área grande = é uma câmera, não apresentação


def load_layout(path=None):
    with open(path or DEFAULT_LAYOUT, "r", encoding="utf-8") as f:
        return json.load(f)


def even(v):
    return int(v) // 2 * 2


# ---------------------------------------------------------------- detecção do layout

def strong_peaks(profile, thr=PEAK_T):
    """Posições dos picos (agrupando colunas vizinhas) com valor >= thr."""
    peaks, group = [], []
    for i in np.where(profile >= thr)[0]:
        if group and i - group[-1] > 2:
            peaks.append(max(group, key=lambda j: profile[j]))
            group = []
        group.append(i)
    if group:
        peaks.append(max(group, key=lambda j: profile[j]))
    return [int(p) for p in peaks]


def _profile(gray, axis):
    if axis == "x":  # bordas verticais, na faixa do meio (evita tarjas e letreiros)
        g = np.abs(cv2.Sobel(gray[130:880], cv2.CV_32F, 1, 0, ksize=3))
        return (g > EDGE_T).mean(axis=0)
    g = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    return (g > EDGE_T).mean(axis=1)


def _column_tiles(gray, x0, x1):
    """Câmeras empilhadas na coluna [x0,x1]: do mesmo tamanho, igualmente espaçadas, separadas por pares de bordas
    a ~5 px. A borda de baixo de uma câmera some quando a roupa/fundo tem a cor da moldura (às vezes a da primeira):
    então o topo de cada câmera vale como emenda (fim de um par, ou borda de cima sozinha e bem forte)."""
    H = gray.shape[0]
    prof = _profile(gray[:, x0 + 15:x1 - 15], "y")
    ys = strong_peaks(prof)
    pairs = [(a, b) for a, b in zip(ys, ys[1:]) if 3 <= b - a <= 12]
    if not pairs:
        return []
    t0 = ys[0]  # topo da primeira câmera
    inicios, fins = {a for a, _ in pairs}, {b for _, b in pairs}
    topos = [y for y in ys if y >= t0 + 100 and (y in fins or (y not in inicios and prof[y] >= SO_TOPO_T))]
    emenda = int(np.median([b - a for a, b in pairs]))

    def sequencia(passo):  # câmeras a cada 'passo' a partir da primeira
        tiles = [t0]
        while True:
            t = next((y for y in topos if abs(y - (tiles[-1] + passo)) <= 15), None)
            if t is None or t + passo - emenda > H:
                return tiles
            tiles.append(t)

    # o espaçamento certo é o que forma a sequência mais longa (uma linha forte DENTRO de uma câmera também vira
    # candidata, mas não se repete); empate: a que usa mais emendas completas (pares de bordas)
    melhor = max((sequencia(c - t0) for c in topos[:4]), key=lambda ts: (len(ts), sum(t in fins for t in ts)), default=[])
    if len(melhor) < 2:
        return []
    h = melhor[1] - t0 - emenda  # altura = espaçamento menos a emenda
    return [(x0, t, x1 - x0, h) for t in melhor]


def parse_frame(gray):
    """Lê a geometria do layout no quadro (cinza, 1920x1080)."""
    H, W = gray.shape
    if (W, H) != (1920, 1080):  # a geometria do canal (posições em pixels) só existe em 1080p
        return {"tipo": "outro", "w": W, "h": H}
    xs = strong_peaks(_profile(gray, "x"))

    # Coluna lateral de câmeras pequenas (esquerda ou direita)
    for side in ("esq", "dir"):
        # A prova é a coluna ter >= 2 câmeras empilhadas; a área grande começa na próxima borda (ou logo após a coluna)
        if side == "esq":
            edge = [x for x in xs if x <= 40]
            inner = [x for x in xs if 250 <= x <= 520]
            big = lambda b: min([c for c in xs if b + 3 <= c <= b + GRANDE_GAP], default=b + 17)
        else:
            edge = [x for x in xs if x >= W - 40]
            inner = [x for x in xs if W - 520 <= x <= W - 250]
            big = lambda b: max([c for c in xs if b - GRANDE_GAP <= c <= b - 3], default=b - 10)
        for b in inner:
            if not edge:
                continue
            x0, x1 = (edge[0], b) if side == "esq" else (b, edge[-1])
            tiles = _column_tiles(gray, x0, x1)
            if len(tiles) >= 2:
                gx0, gx1 = (big(b), W - 10) if side == "esq" else (10, big(b))
                # Topo real da área grande (primeira borda horizontal forte); sem isso entra a faixa escura do fundo
                gy0 = min([y for y in strong_peaks(_profile(gray[:, gx0 + 50:gx1 - 50], "y")) if 20 <= y <= 300], default=40) + 2
                return {"tipo": "lateral", "lado": side, "pequenos": tiles, "grande": (gx0, gy0, gx1 - gx0, H - 40 - gy0)}

    # Quadros lado a lado: bordas em W/3 e 2W/3 (3 pessoas) ou W/2 (2 pessoas).
    # Uma borda de um lado da divisão às vezes some (fundo da câmera parecido com a moldura): com 3 quadros
    # basta 1 borda forte por divisão (2 coincidências); com 2 quadros exige o par (linha isolada no meio é comum em vídeo).
    for n, need in ((3, 1), (2, 2)):
        divs = [[x for x in xs if abs(x - W * k / n) <= 15] for k in range(1, n)]
        if all(len(d) >= need for d in divs):
            cuts = [0]
            for k, d in enumerate(divs, 1):
                if len(d) >= 2:
                    cuts += [min(d), max(d)]
                elif d[0] < W * k / n:   # só a borda do quadro da esquerda apareceu
                    cuts += [d[0], d[0] + 8]
                else:                    # só a borda do quadro da direita
                    cuts += [d[0] - 8, d[0]]
            cuts.append(W)
            return {"tipo": "quadros", "h": H, "tiles": [(cuts[i], cuts[i + 1] - cuts[i]) for i in range(0, len(cuts), 2)]}

    return {"tipo": "outro", "w": W, "h": H}


def layout_key(lay):
    """Assinatura para agrupar quadros do mesmo layout (tolerante a 1-2 px)."""
    if lay["tipo"] == "quadros":
        return f"quadros{len(lay['tiles'])}"
    if lay["tipo"] == "lateral":
        return f"lateral-{lay['lado']}-{len(lay['pequenos'])}-{lay['grande'][0] // 16}"
    return "pessoa" if lay["tipo"] == "pessoa" else "outro"


def smooth_runs(times, keys, window, min_run, outro_gap=5.0):
    """Filtro de moda (±window amostras), junta trechos curtos (< min_run s) ao anterior e absorve
    'outro' de até outro_gap s entre dois trechos do mesmo layout (borda perdida por instantes). -> [(t0, t1, chave)]"""
    sm = [Counter(keys[max(0, i - window):i + window + 1]).most_common(1)[0][0] for i in range(len(keys))]
    runs = []
    for t, k in zip(times, sm):
        if runs and runs[-1][2] == k:
            runs[-1][1] = t
        else:
            runs.append([t, t, k])
    merged = []
    for r in runs:
        if merged and (r[1] - r[0] < min_run):
            merged[-1][1] = r[1]
        else:
            if merged:
                merged[-1][1] = r[0]
            merged.append(r)
    if merged and len(merged) > 1 and merged[0][1] - merged[0][0] < min_run:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    i = 1
    while i < len(merged) - 1:
        prev, cur, nxt = merged[i - 1], merged[i], merged[i + 1]
        if cur[2] == "outro" and prev[2] == nxt[2] and nxt[0] - cur[0] <= outro_gap:
            prev[1] = nxt[1]
            del merged[i:i + 2]
        else:
            i += 1
    # Trechos seguidos com o mesmo layout viram um só (senão vira um corte à toa na timeline)
    final = []
    for r in merged:
        if final and final[-1][2] == r[2]:
            final[-1][1] = r[1]
        else:
            final.append(r)
    return [tuple(r) for r in final]


# ---------------------------------------------------------------- quem fala (layout de quadros)

def pick_speakers(times, activity, min_shot=2.0, hold=0.6, min_activity=0.004, ratio=1.5):
    """times: segundos de cada amostra; activity: [atividade por quadro] por amostra (já suavizada).
    Retorna [(t_inicio, quadro)]. Troca só quando outra pessoa fala `ratio`x mais que a atual por >= hold s
    e o plano atual já durou >= min_shot s (evita o zoom piscar em debate com fala sobreposta)."""
    if not len(times):
        return [(0.0, 0)]
    act = np.asarray(activity, dtype=float)
    first = [i for i, t in enumerate(times) if t <= times[0] + min_shot]
    shots = [(0.0, int(np.argmax(act[first].sum(axis=0))))]
    cand, cand_since = None, None
    for t, a in zip(times, act):
        best = int(np.argmax(a))
        if best == shots[-1][1] or a[best] < min_activity or a[best] < ratio * a[shots[-1][1]]:
            cand = None
            continue
        if best != cand:
            cand, cand_since = best, t
        if t - cand_since >= hold and t - shots[-1][0] >= min_shot:
            # Troca no momento em que a pessoa começou a falar, respeitando o plano mínimo
            shots.append((max(cand_since, shots[-1][0] + min_shot), best))
            cand = None
    return shots


def speaker_activity(samples, tiles, sample_fps):
    """Movimento da boca por quadro (média móvel de ~1s). samples: [(t, [(bbox, mar), ...])]."""
    mars = []
    for _, faces in samples:
        row, best = [np.nan] * len(tiles), [0] * len(tiles)
        for (x1, y1, x2, y2), mar in faces:
            cx, area = (x1 + x2) / 2, (x2 - x1) * (y2 - y1)
            for k, (tx, tw) in enumerate(tiles):
                if tx <= cx < tx + tw and area > best[k]:
                    best[k], row[k] = area, mar
        mars.append(row)
    return _atividade(np.asarray(mars, dtype=float), sample_fps)


def _atividade(mars, sample_fps):
    """mars: abertura da boca [amostra, pessoa] (nan = sem rosto) -> movimento da boca, média móvel de ~1 s."""
    diff = np.nan_to_num(np.abs(np.diff(mars, axis=0, prepend=mars[:1])), nan=0.0)
    kernel = np.ones(max(1, sample_fps)) / max(1, sample_fps)
    return np.stack([np.convolve(diff[:, k], kernel, mode="same") for k in range(diff.shape[1])], axis=1)


def crop_x_expr(shots, tiles_x):
    """Expressão do ffmpeg para o x do recorte em função do tempo (t)."""
    expr = str(tiles_x[shots[-1][1]])
    for i in range(len(shots) - 1, 0, -1):
        expr = f"if(lt(t,{shots[i][0]:.3f}),{tiles_x[shots[i - 1][1]]},{expr})"
    return expr


# ---------------------------------------------------------------- quem aparece (tela cheia, qualquer vídeo)

def rastrear(times, faces, sample_fps):
    """Liga cada rosto ao da amostra anterior (mesma pessoa = mesmo lugar da tela, visto há no máximo 1 s).
    -> por amostra: {pista: (centro x, altura do rosto, boca)}. Saem rostos de fundo (menos de 1/3 do maior
    da amostra) e pistas com menos de 0,5 s (detecção falsa, alguém passando)."""
    pistas, obs = [], []  # pista = [cx, cy, largura, último t]
    for t, fs in zip(times, faces):
        fs = fs or []
        maior = max((b[3] - b[1] for b, _ in fs), default=0)
        linha = {}
        for b, mar in sorted(fs, key=lambda f: f[0][1] - f[0][3]):  # os maiores escolhem a pista primeiro
            if b[3] - b[1] < maior / 3:
                continue
            cx, cy, w = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[2] - b[0]
            dist = lambda k: max(abs(pistas[k][0] - cx), abs(pistas[k][1] - cy))
            livres = [k for k, p in enumerate(pistas) if k not in linha and t - p[3] <= 1.0 and dist(k) < 0.7 * max(w, p[2])]
            k = min(livres, key=dist, default=len(pistas))
            pistas[k:k + 1] = [[cx, cy, w, t]]
            linha[k] = (cx, b[3] - b[1], mar)
        obs.append(linha)
    vistas = Counter(k for linha in obs for k in linha)
    return [{k: v for k, v in linha.items() if vistas[k] >= sample_fps / 2} for linha in obs]


def seguir_rostos(times, faces, W, H, cfg):
    """Enquadramento vertical de um trecho de tela cheia. times: segundos desde o início do trecho;
    faces: [(caixa, boca)] por amostra. -> [(t, cx | None)]: a partir de t o recorte fica centrado em cx
    (None = ninguém na tela: quadro inteiro). O recorte é fixo e só muda (corte seco) quando:
      - outra pessoa fala (mesma regra do zoom dos quadros: ratio/hold/plano mínimo);
      - a pessoa seguida some (corte de cena): vai na hora para quem está na tela, falando ou o maior rosto;
      - a pessoa sai do meio do recorte por 0,5 s (andou, a câmera mexeu).
    Mudança que cabe no recorte atual não gera troca."""
    # ponytail: corte seco ao reenquadrar; se incomodar em vídeo com muita câmera na mão, interpolar o x entre trocas
    fps = cfg.get("sample_fps", 8)
    obs = rastrear(times, faces, fps)
    n = 1 + max((k for linha in obs for k in linha), default=-1)
    if not n:
        return [(0.0, None)]
    mars = np.full((len(obs), n), np.nan)
    for i, linha in enumerate(obs):
        for k, (_, _, mar) in linha.items():
            mars[i, k] = mar
    act = _atividade(mars, fps)
    min_shot, hold = cfg.get("min_shot_seconds", 3.0), cfg.get("switch_hold_seconds", 0.6)
    min_act, ratio = cfg.get("min_activity", 0.004), cfg.get("switch_ratio", 1.5)
    folga = cfg.get("seguir_folga", 0.3) * min(W, H * OUT_W / OUT_H)  # quanto o rosto pode sair do centro do recorte
    sem_rosto = cfg.get("sem_rosto_seconds", 1.5)

    def centro(i, k):  # posição estável: mediana da pista no próximo ~1 s
        return float(np.median([linha[k][0] for linha in obs[i:i + fps] if k in linha]))

    def melhor(i):  # quem fala; ninguém falando: o maior rosto
        k = max(obs[i], key=lambda k: act[i, k])
        return k if act[i, k] >= min_act else max(obs[i], key=lambda k: obs[i][k][1])

    out = []

    def marcar(t, c):  # -> centro em vigor
        if out and c is not None and out[-1][1] is not None and abs(c - out[-1][1]) <= folga / 2:
            return out[-1][1]  # cabe no recorte atual
        if out and t <= out[-1][0]:
            out[-1] = (out[-1][0], c)
        else:
            out.append((t, c))
        return c

    atual = alvo = cand = cand_t = fora_t = None
    visto, sumiu = -1e9, times[0]  # último instante com a pista atual; primeiro instante sem ela
    for i, t in enumerate(times):
        linha = obs[i]
        if atual in linha:
            visto, sumiu = t, None
            k = melhor(i)
            if k != atual and act[i, k] >= min_act and act[i, k] >= ratio * act[i, atual]:
                if k != cand:
                    cand, cand_t = k, t
                if t - cand_t >= hold and t - out[-1][0] >= min_shot:
                    atual, alvo = k, marcar(max(cand_t, out[-1][0] + min_shot), centro(i, k))
                    cand = fora_t = None
                continue
            cand = None
            if abs(linha[atual][0] - alvo) > folga:
                fora_t = t if fora_t is None else fora_t
                if t - fora_t >= 0.5:
                    alvo, fora_t = marcar(t, centro(i, atual)), None
            else:
                fora_t = None
            continue
        cand = fora_t = None
        sumiu = t if sumiu is None else sumiu
        if linha and t - visto >= 0.25:        # corte de cena (ou primeira pessoa): direto para quem está na tela
            atual = melhor(i)
            alvo = marcar(sumiu if out and out[-1][1] is not None else t, centro(i, atual))
            visto, sumiu = t, None
        elif atual is not None and t - visto >= sem_rosto:
            marcar(sumiu, None)
            atual = None
    if not out:
        return [(0.0, None)]
    # rosto que aparece por pouco tempo entre dois trechos sem ninguém: continua quadro inteiro
    limpo = []
    for j, (t, c) in enumerate(out):
        prox = out[j + 1] if j + 1 < len(out) else (times[-1], 0)
        curto = c is not None and prox[1] is None and prox[0] - t < sem_rosto and (not limpo or limpo[-1][1] is None)
        if curto or (limpo and c is None and limpo[-1][1] is None):
            continue
        limpo.append((t, c))
    limpo = limpo or [(0.0, None)]
    limpo[0] = (0.0, limpo[0][1])
    return limpo


def planos_tela_cheia(times, faces, t0, t1, W, H, cfg):
    """Trecho de tela cheia [t0, t1] -> planos "pessoa" (recorte em quem aparece) e "outro" (ninguém na tela)."""
    marcas = seguir_rostos(times, faces, W, H, cfg) if times and cfg.get("seguir_rostos", True) else [(0.0, None)]
    plans = []
    for t, c in marcas:
        if plans and plans[-1]["tipo"] == "pessoa" and c is not None:
            plans[-1]["shots"].append([float(t0 + t - plans[-1]["t0"]), c])
            continue
        if plans:
            plans[-1]["t1"] = float(t0 + t)
        plans.append({"tipo": "outro", "w": W, "h": H, "t0": float(t0 + t), "t1": float(t1)} if c is None else
                     {"tipo": "pessoa", "w": W, "h": H, "t0": float(t0 + t), "t1": float(t1), "shots": [[0.0, c]]})
    return plans


# ---------------------------------------------------------------- montagem (camadas)
#
# layers() descreve cada camada do trecho (recorte no vídeo original -> retângulo na tela vertical).
# É a fonte única: build_filter() monta o ffmpeg a partir dela e o export do Premiere gera o XML a partir dela.

def area_apresentacao(plan, cfg):
    """Área grande mostrada inteira, sem os últimos pixels de baixo: ali começa a faixa rolante do programa
    ("APOIE O JORNALISMO..."), que aparecia como uma tira cortada. Corte fixo em layout_canal.json."""
    gx, gy, gw, gh = plan["grande"]
    return gx, gy, gw, gh - cfg.get("apresentacao_corte_baixo", 20)


def lateral_geometry(plan, cfg):
    """Bloco central = área grande (apresentação inteira ou zoom na câmera) com as câmeras pequenas coladas embaixo.
    -> (altura da área grande, altura da faixa de câmeras, y do topo do bloco)"""
    n = len(plan["pequenos"])
    band = even(OUT_W // n * 3 / 4)
    if plan.get("rosto"):
        fg_h = even(cfg.get("lateral_camera_h", 1200))
    else:
        _, _, gw, gh = area_apresentacao(plan, cfg)
        fg_h = even(OUT_W * gh / gw)
    fg_h = min(fg_h, OUT_H - band)
    return fg_h, band, even((OUT_H - fg_h - band) / 2)


def layers(plan, cfg):
    """Camadas do trecho, de baixo para cima. Cada uma: {nome, src: (x,y,w,h) no original, dst: (x,y,w,h) na tela,
    fundo: True se é o fundo desfocado (src é ampliado até cobrir a tela e desfocado), trocas: [(t, x)] se o x do recorte muda}."""
    W = plan.get("w", 1920)
    H = plan.get("h", 1080)
    if plan["tipo"] == "quadros":
        tiles = plan["tiles"]
        tw = min(w for _, w in tiles)
        bottom_h = even(OUT_W * H / W)
        top_h = OUT_H - bottom_h
        # Recorte com a proporção da área de cima, terminando acima das tarjas/letreiro; em quadros largos (2 pessoas)
        # sobra largura, então o recorte segue o rosto da pessoa
        ch = even(min(tw * top_h / OUT_W, cfg.get("lateral_max_y", 800) - cfg["zoom_y"]))
        cw = even(ch * OUT_W / top_h)
        centers = plan.get("centros") or [None] * len(tiles)
        xs = [int(np.clip((c if c is not None else x + w / 2) - cw / 2, x, x + w - cw)) for (x, w), c in zip(tiles, centers)]
        trocas = [(t, xs[q]) for t, q in plan["shots"]]
        q0 = plan["shots"][0][1]
        if perto(plan, q0, cfg):  # rosto grande: menos zoom, mostra mais do corpo; sobra nas laterais vira fundo desfocado
            z = cfg.get("quadros_zoom_perto", 1.4)
            cw, ch = even(min(tw, OUT_W / z)), even(min(top_h / z, H))
            xs = [int(np.clip((c if c is not None else x + w / 2) - cw / 2, x, x + w - cw)) for (x, w), c in zip(tiles, centers)]
            trocas = [(t, xs[q]) for t, q in plan["shots"]]
            cy = (plan.get("centros_y") or [None] * len(tiles))[q0] or H * 0.4
            y = int(np.clip(cy - ch * 0.45, 0, max(0, min(H, cfg.get("quadros_max_y_perto", 940)) - ch)))
            dw, dh = even(cw * z), even(ch * z)
            return [
                {"nome": "Fundo desfocado", "src": (xs[q0], y, cw, ch), "dst": (0, 0, OUT_W, top_h), "fundo": True},
                {"nome": "Zoom em quem fala", "src": (xs[q0], y, cw, ch), "dst": (even((OUT_W - dw) / 2), even((top_h - dh) / 2), dw, dh), "trocas": trocas},
                {"nome": "Vídeo inteiro", "src": (0, 0, W, H), "dst": (0, top_h, OUT_W, bottom_h)},
            ]
        return [
            {"nome": "Zoom em quem fala", "src": (trocas[0][1], cfg["zoom_y"], cw, ch), "dst": (0, 0, OUT_W, top_h), "trocas": trocas},
            # vídeo inteiro por último = camada de cima (o zoom nunca o cobre)
            {"nome": "Vídeo inteiro", "src": (0, 0, W, H), "dst": (0, top_h, OUT_W, bottom_h)},
        ]
    if plan["tipo"] == "lateral":
        fg_h, band, top = lateral_geometry(plan, cfg)
        gx, gy, gw, gh = plan["grande"]
        if plan.get("rosto"):  # câmera: zoom no rosto, terminando acima das tarjas ("AO VIVO", manchete)
            fx, fy = plan["rosto"]
            max_y = min(gy + gh, cfg.get("lateral_max_y", 800))
            ch = even(min(max_y - gy, cfg.get("zoom_h_lateral", 700)))
            cw = even(min(gw, ch * OUT_W / fg_h))
            big = (int(np.clip(fx - cw / 2, gx, gx + gw - cw)), int(np.clip(fy - ch * 0.4, gy, max_y - ch)), cw, ch)
            nome = "Câmera do convidado"
        else:                  # apresentação/vídeo: conteúdo inteiro
            big, nome = area_apresentacao(plan, cfg), "Apresentação"
        out = [{"nome": "Fundo desfocado", "src": big, "dst": (0, 0, OUT_W, OUT_H), "fundo": True},
               {"nome": nome, "src": big, "dst": (0, top, OUT_W, fg_h)}]
        sw = OUT_W // len(plan["pequenos"])
        for i, (x, y, w, h) in enumerate(plan["pequenos"]):
            # Todas iguais: a câmera inteira, na mesma escala e no mesmo lugar (preferência do usuário: consistência,
            # mesmo que entre um pedaço do letreiro do programa em alguma delas). 3% de baixo: linha da moldura.
            ch = even(h * 0.97)
            cw = even(min(w, ch * 4 / 3))
            out.append({"nome": f"Câmera pequena {i + 1}", "src": (x + (w - cw) // 2, y, cw, ch), "dst": (i * sw, top + fg_h, sw, band)})
        return out
    if plan["tipo"] == "pessoa":  # recorte vertical na altura toda, centrado em quem aparece (centro x em shots)
        cw = even(min(W, H * OUT_W / OUT_H))
        trocas = [(t, int(np.clip(c - cw / 2, 0, W - cw))) for t, c in plan["shots"]]
        return [{"nome": "Zoom em quem fala", "src": (trocas[0][1], 0, cw, even(H)), "dst": (0, 0, OUT_W, OUT_H), "trocas": trocas}]
    fit_h = even(OUT_W * H / W)  # outro: quadro inteiro na largura, centralizado sobre ele mesmo desfocado
    return [{"nome": "Fundo desfocado", "src": (0, 0, W, H), "dst": (0, 0, OUT_W, OUT_H), "fundo": True},
            {"nome": "Vídeo inteiro", "src": (0, 0, W, H), "dst": (0, even((OUT_H - fit_h) / 2), OUT_W, fit_h)}]


def build_filter(plan, cfg, only_fundo=False):
    """Filtro ffmpeg do trecho a partir de layers(). only_fundo: só o fundo desfocado (preto se não houver)."""
    ls = [l for l in layers(plan, cfg) if l.get("fundo")] if only_fundo else layers(plan, cfg)
    if not ls:
        return f"[0:v]setpts=PTS-STARTPTS,scale={OUT_W}:{OUT_H},drawbox=c=black:t=fill,format=yuv420p[v]"
    parts = [f"[0:v]setpts=PTS-STARTPTS,split={len(ls)}" + "".join(f"[i{k}]" for k in range(len(ls))) + ";"]
    for k, l in enumerate(ls):
        sx, sy, sw, sh = l["src"]
        dx, dy, dw, dh = l["dst"]
        x = sx
        if l.get("trocas"):  # x do recorte muda com o tempo (troca de quem fala); troca i usa o x da troca i
            x = "'" + crop_x_expr([(t, i) for i, (t, _) in enumerate(l["trocas"])], [tx for _, tx in l["trocas"]]) + "'"
        chain = f"[i{k}]crop={sw}:{sh}:{x}:{sy},"
        if l.get("fundo"):
            # desfoque numa cópia 8x menor e ampliada de volta: mesmo visual do boxblur 25 em tamanho cheio, ~5x mais rápido
            pw, ph = max(2, dw // 8), max(2, dh // 8)
            chain += (f"scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph},boxblur=3,"
                      f"scale={dw}:{dh}:flags=bicubic,setsar=1")
        else:
            chain += f"scale={dw}:{dh},setsar=1"
        if k == 0:
            chain += f",pad={OUT_W}:{OUT_H}:{dx}:{dy}[c0];"
        else:
            chain += f"[l{k}];[c{k - 1}][l{k}]overlay={dx}:{dy}[c{k}];"
        parts.append(chain)
    parts.append(f"[c{len(ls) - 1}]format=yuv420p[v]")
    return "".join(parts)


def build_filter_gpu(plan, cfg, only_fundo=False):
    """O mesmo de build_filter, todo na placa (quadros CUDA do decodificador até o NVENC, sem passar pela CPU).
    Desfoque: não há boxblur em CUDA, só a cópia reduzida passa pela CPU. None = trecho sem camadas (usa a CPU)."""
    ls = [l for l in layers(plan, cfg) if l.get("fundo")] if only_fundo else layers(plan, cfg)
    if not ls:
        return None
    par = lambda v: int(v) // 2 * 2  # NV12: recorte em coordenadas pares
    W, H = plan.get("w", 1920), plan.get("h", 1080)
    # tela base 1080x1920 do próprio quadro (as camadas cobrem a tela inteira por cima dela)
    parts = [f"[0:v]setpts=PTS-STARTPTS,split={len(ls) + 1}[base]" + "".join(f"[i{k}]" for k in range(len(ls))) + ";",
             f"[base]scale_cuda={OUT_W}:{OUT_H}:format=yuv420p[c];"]
    for k, l in enumerate(ls):
        sx, sy, sw, sh = l["src"]
        dx, dy, dw, dh = l["dst"]
        if l.get("fundo"):  # cobre a tela: recorta o centro do src na proporção da tela
            if sw / sh > dw / dh:
                cw, ch = sh * dw / dh, sh
            else:
                cw, ch = sw, sw * dh / dw
            cx, cy = sx + (sw - cw) / 2, sy + (sh - ch) / 2
            # A placa reduz o quadro INTEIRO 5x (o crop em CUDA só marca a área e a marca é reaplicada ao descer
            # para a CPU: imagem errada); a CPU recorta, desfoca a cópia minúscula e devolve em yuv420p
            # (nv12 subido pelo hwupload_cuda chega com o croma trocado) para a placa ampliar.
            pw = -(-dw // 128) * 16              # ~1/8 da tela, largura múltipla de 16 (senão sobra um fio verde na borda)
            chain = (f"[i{k}]scale_cuda={par(W / 5)}:{par(H / 5)},hwdownload,format=nv12,"
                     f"crop={par(cw / 5)}:{par(ch / 5)}:{par(cx / 5)}:{par(cy / 5)},scale={pw}:{par(pw * dh / dw)},boxblur=3,"
                     f"format=yuv420p,hwupload_cuda,scale_cuda={dw}:{dh}:interp_algo=bicubic")
        else:
            x = par(sx)
            if l.get("trocas"):
                x = "'" + crop_x_expr([(t, i) for i, (t, _) in enumerate(l["trocas"])], [par(tx) for _, tx in l["trocas"]]) + "'"
            chain = f"[i{k}]crop={par(sw)}:{par(sh)}:{x}:{par(sy)},scale_cuda={dw}:{dh}:format=yuv420p"
        chain += f"[l{k}];[{'c' if k == 0 else f'c{k - 1}'}][l{k}]overlay_cuda={dx}:{dy}[c{k}];"
        parts.append(chain)
    # overlay_cuda/pad_cuda entregam o quadro com a largura arredondada (1088): cópia no tamanho exato
    parts.append(f"[c{len(ls) - 1}]crop={OUT_W}:{OUT_H}:0:0,scale_cuda={OUT_W}:{OUT_H}:passthrough=0,setsar=1[v]")
    return "".join(parts)


def subtitle_pos(plan, cfg):
    """(y, alinhamento ASS) da legenda nos trechos laterais, em PlayRes 640 do adjust_subtitles; None = padrão.
    Alinhamento 2 = y é a base do texto; 8 = y é o topo do texto."""
    if plan["tipo"] != "lateral":
        return None
    fg_h, band, top = lateral_geometry(plan, cfg)
    if plan.get("rosto"):
        return int((top + fg_h - 40) * 640 / OUT_H), 2   # na base da câmera do convidado
    return int((top + fg_h + band + 30) * 640 / OUT_H), 8  # logo abaixo das câmeras pequenas



# ---------------------------------------------------------------- análise + renderização

def init_face_app():
    import torch  # noqa: F401 - carrega as DLLs CUDA/cuDNN do torch; sem isso o onnxruntime-gpu cai na CPU (~7x mais lento)
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "landmark_3d_68"],
                       providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


AMOSTRA_V = 4  # mude quando parse_frame ou a leitura de rostos mudarem: invalida os caches de amostras
BLOCO = 8      # amostras por bloco (~1 s): o layout é lido 1x por bloco
REDETECTAR = 1  # em quadros, rostos achados a cada N amostras (a boca é medida em todas). Testado 2 e 4: caixa velha
                # mede a boca fora do lugar e aparecem trocas de quem fala que a detecção fresca não faz


def _rostos(app, frame):
    """Caixas dos rostos no quadro (o detector é a parte cara: roda 1x por bloco)."""
    caixas, _ = app.det_model.detect(frame, max_num=0, metric="default")
    return [[round(float(v), 1) for v in b[:4]] for b in caixas]


def abertura_boca(lm):
    """Altura / largura da parte de dentro dos lábios (pontos 60-67 dos 68 do InsightFace)."""
    w = np.linalg.norm(lm[60][:2] - lm[64][:2])
    return 0.0 if w < 1e-6 else float(np.linalg.norm(lm[62][:2] - lm[66][:2]) / w)


def _bocas(app, frame, caixas):
    """Abertura da boca de cada rosto, nas caixas achadas há no máximo REDETECTAR amostras."""
    from insightface.app.common import Face
    out = []
    for b in caixas:
        f = Face(bbox=np.array(b))
        app.models["landmark_3d_68"].get(frame, f)
        out.append([b, round(float(abertura_boca(f["landmark_3d_68"])), 5)])
    return out


def _layouts_do_bloco(frames, anterior):
    """Layout de cada quadro do bloco lendo poucos: o último e, se mudou desde o bloco anterior,
    busca binária até achar o quadro da troca. Layout que aparece e some dentro de 1 s não é visto
    (a suavização descartaria de qualquer forma: trecho mínimo de 1,5 s)."""
    lidos = {}
    def ler(i):
        if i not in lidos:
            lidos[i] = parse_frame(cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY))
        return lidos[i]
    def chave(i):
        return layout_key(anterior if i < 0 else ler(i))
    def buscar(lo, hi):  # chave(lo) != chave(hi): acha as trocas entre os dois
        if hi - lo <= 1:
            return
        meio = (lo + hi) // 2
        if chave(meio) != chave(lo):
            buscar(lo, meio)
        if chave(meio) != chave(hi):
            buscar(meio, hi)
    n = len(frames)
    inicio = -1 if anterior else 0
    if chave(inicio) != chave(n - 1):
        buscar(inicio, n - 1)
    atual, out = anterior, []
    for i in range(n):
        atual = lidos.get(i, atual)
        out.append(atual)
    return out


def amostrar(corte, trechos, rostos, cache, sample_fps=8):
    """Amostras de um corte feito colando os trechos [s, e] (segundos da live) -> (tempos no corte, layouts, rostos).
    O cache é indexado pelo quadro da LIVE ({"<quadro>": [layout, rostos]}, grade fixa de 1 a cada `passo` quadros):
    o que já foi analisado, neste assunto antes da edição ou em outro, não é lido de novo.
    Rostos só onde são usados: quadros e tela cheia = [(caixa, boca)] em toda amostra (quem fala); lateral =
    [(caixa, nan)] 1x por bloco (tem câmera na área grande?). Amostra sem rostos medidos = None.
    rostos() devolve o detector (só é carregado se faltar alguma amostra)."""
    cap = cv2.VideoCapture(corte)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    passo = max(1, round(fps / sample_fps))
    alvos, base = [], 0  # (quadro no corte, quadro na live)
    for s, e in trechos:
        S, E = round(s * fps), round(e * fps)
        alvos += [(base + k - S, k) for k in range(-(-S // passo) * passo, E, passo)]
        base += E - S
    pos, anterior = None, None
    for b in range(0, len(alvos), BLOCO):
        bloco = alvos[b:b + BLOCO]
        if all(str(k) in cache for _, k in bloco):
            anterior = cache[str(bloco[-1][1])][0]
            continue
        frames = []
        for j, _ in bloco:
            if pos is None or not pos <= j <= pos + 10 * fps:  # longe: salta; perto: decodifica até lá
                cap.set(cv2.CAP_PROP_POS_FRAMES, j)
                pos = j
            while pos < j and cap.grab():
                pos += 1
            ok, frame = cap.read()
            pos += 1
            if not ok:
                break
            frames.append(frame)
        if not frames:
            break
        lays = _layouts_do_bloco(frames, anterior)
        caixas = None
        for i, ((_, k), frame, lay) in enumerate(zip(bloco, frames, lays)):
            boca = lay["tipo"] in ("quadros", "outro")  # quem fala: zoom dos quadros / quem seguir na tela cheia
            if i == 0 or layout_key(lay) != layout_key(lays[i - 1]) or (boca and i % REDETECTAR == 0):
                caixas = _rostos(rostos(), frame)
                fs = [[c, float("nan")] for c in caixas]
            else:
                fs = None
            if boca:
                fs = _bocas(rostos(), frame, caixas)
            cache[str(k)] = [lay, fs]
        anterior = lays[-1]
    cap.release()
    ok = [(j / fps, cache[str(k)]) for j, k in alvos if str(k) in cache]
    return [t for t, _ in ok], [a[0] for _, a in ok], [a[1] for _, a in ok]


def medidas_rostos(tiles, faces):
    """Por quadro: mediana do centro x, do centro y e da altura dos rostos (None = sem rosto). faces: [[(bbox, mar)]]."""
    med = lambda v: float(np.median(v)) if v else None
    out = {"centros": [], "centros_y": [], "alturas": []}
    for tx, tw in tiles:
        bs = [b for fs in faces for b, _ in fs or [] if tx <= (b[0] + b[2]) / 2 < tx + tw]
        out["centros"].append(med([(b[0] + b[2]) / 2 for b in bs]))
        out["centros_y"].append(med([(b[1] + b[3]) / 2 for b in bs]))
        out["alturas"].append(med([b[3] - b[1] for b in bs]))
    return out


def perto(plan, q, cfg):
    """A pessoa do quadro q está perto da câmera: o maior rosto do trecho, acima do limite. Só o maior porque quem está
    longe às vezes chega perto do limite ao se inclinar para a câmera (medido: 462-469 px, contra 470-540 da mais perto)."""
    alturas = plan.get("alturas") or []
    if q >= len(alturas) or alturas[q] is None:
        return False
    return alturas[q] >= cfg.get("quadros_rosto_grande", 460) and alturas[q] >= max(a for a in alturas if a is not None)


def dividir_por_zoom(plans, cfg):
    """Trecho de quadros em que o zoom alterna entre quem está perto e quem não está: um trecho para cada (o recorte
    do ffmpeg tem tamanho fixo dentro de um trecho)."""
    out = []
    for p in plans:
        if p["tipo"] != "quadros" or len({perto(p, q, cfg) for _, q in p["shots"]}) < 2:
            out.append(p)
            continue
        fim = p.get("t1", p["t0"])
        grupos = []
        for s, q in p["shots"]:
            if grupos and perto(p, grupos[-1][-1][1], cfg) == perto(p, q, cfg):
                grupos[-1].append([s, q])
            else:
                grupos.append([[s, q]])
        for g, prox in zip(grupos, grupos[1:] + [None]):
            t0 = p["t0"] + g[0][0]
            out.append(dict(p, t0=t0, t1=p["t0"] + prox[0][0] if prox else fim, shots=[[s - g[0][0], q] for s, q in g]))
    return out


MAX_TROCAS = 40  # trocas por trecho na montagem: cada uma é um if() aninhado na expressão do ffmpeg


def dividir_longos(plans):
    """Trecho com trocas demais (vlog com muitos cortes de cena) vira vários, de até MAX_TROCAS cada."""
    out = []
    for p in plans:
        while len(p.get("shots") or []) > MAX_TROCAS:
            d = p["shots"][MAX_TROCAS][0]
            out.append(dict(p, t1=p["t0"] + d, shots=p["shots"][:MAX_TROCAS]))
            p = dict(p, t0=p["t0"] + d, shots=[[s - d, k] for s, k in p["shots"][MAX_TROCAS:]])
        out.append(p)
    return out


def planejar(times, layouts, faces, dur, cfg):
    """Amostras -> planos (um por trecho de layout), cobrindo [0, dur]."""
    if not times:
        return [{"t0": 0.0, "t1": dur, "tipo": "outro"}]
    sample_fps = cfg.get("sample_fps", 8)
    keys = [layout_key(l) for l in layouts]
    runs = smooth_runs(times, keys, window=sample_fps, min_run=cfg.get("min_layout_seconds", 1.5),
                       outro_gap=cfg.get("outro_gap_seconds", 5.0))
    plans = []
    for i, (t0, t1, key) in enumerate(runs):
        t0 = 0.0 if i == 0 else t0
        t1 = runs[i + 1][0] if i + 1 < len(runs) else dur
        idxs = [j for j, t in enumerate(times) if t0 <= t < t1]
        same = [layouts[j] for j in idxs if keys[j] == key] or [layouts[idxs[0]]]
        plan = dict(same[len(same) // 2])  # geometria de um quadro típico do trecho
        plan.update(t0=float(t0), t1=float(t1))
        if plan["tipo"] == "outro" and "w" in plan:
            plans += planos_tela_cheia([times[j] - t0 for j in idxs], [faces[j] for j in idxs], t0, t1, plan["w"], plan["h"], cfg)
            continue
        if plan["tipo"] == "quadros":
            plan.update(medidas_rostos(plan["tiles"], [faces[j] for j in idxs]))
            act = speaker_activity([(times[j], faces[j] or []) for j in idxs], plan["tiles"], sample_fps)
            plan["shots"] = pick_speakers([times[j] - t0 for j in idxs], act, cfg.get("min_shot_seconds", 3.0),
                                          cfg.get("switch_hold_seconds", 0.6), cfg.get("min_activity", 0.004),
                                          cfg.get("switch_ratio", 1.5))
        elif plan["tipo"] == "lateral":
            gx, gy, gw, gh = plan["grande"]
            centers, medidos = [], [j for j in idxs if faces[j] is not None]
            for j in medidos:
                big = [b for b, _ in faces[j] if gx <= (b[0] + b[2]) / 2 < gx + gw and b[3] - b[1] >= BIG_FACE_H]
                if big:
                    b = max(big, key=lambda b: b[3] - b[1])
                    centers.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
            # Padrão: área grande inteira (apresentação com fotos e chamadas não pode virar zoom num rosto e cortar
            # os textos). O rosto achado fica guardado: "Câmera (zoom no rosto)" na etapa 3 usa ele (convidado).
            if centers and len(centers) >= 0.4 * len(medidos):
                plan["rosto_guardado"] = tuple(float(v) for v in np.median(centers, axis=0))
        plans.append(plan)
    return plans


GPU_ENTRADA = ["-init_hw_device", "cuda=gpu", "-filter_hw_device", "gpu",
               "-hwaccel", "cuda", "-hwaccel_device", "gpu", "-hwaccel_output_format", "cuda"]
usar_gpu = True  # vira False na primeira falha da montagem na placa (driver, outro PC): o resto vai pela CPU


def render(video_path, output_path, fps, n_frames, plans, cfg, only_fundo=False):
    global usar_gpu
    encoder, preset = ENCODER
    work = output_path + ".parts"
    os.makedirs(work, exist_ok=True)
    seg_files = []
    for i, plan in enumerate(plans):
        f0 = 0 if i == 0 else round(plan["t0"] * fps)  # o vídeo inteiro entra, senão o áudio fica adiantado
        f1 = n_frames if i == len(plans) - 1 else round(plans[i + 1]["t0"] * fps)
        if f1 <= f0:
            continue
        seg = os.path.join(work, f"seg_{i:03d}.mp4")
        comando = lambda entrada, filtro: (
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entrada,
             "-ss", f"{f0 / fps:.4f}", "-i", video_path, "-frames:v", str(f1 - f0),
             "-filter_complex", filtro, "-map", "[v]", "-an",
             "-r", f"{fps}", "-c:v", encoder, "-preset", preset, "-b:v", "8M", seg])
        filtro_gpu = build_filter_gpu(plan, cfg, only_fundo) if usar_gpu else None
        if filtro_gpu:
            r = subprocess.run(comando(GPU_ENTRADA, filtro_gpu), capture_output=True, text=True)
            if r.returncode != 0:
                usar_gpu = False
                print(f"[Layout do canal] montagem na placa falhou, seguindo pela CPU: {r.stderr.strip()[-300:]}")
        if not filtro_gpu or not usar_gpu:
            subprocess.run(comando([], build_filter(plan, cfg, only_fundo)), check=True)
        seg_files.append(seg)
    lst = os.path.join(work, "lista.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines(f"file '{os.path.abspath(s)}'\n".replace("\\", "/") for s in seg_files)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-i", video_path, "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", output_path], check=True)
    for s in seg_files + [lst]:
        os.remove(s)
    os.rmdir(work)


def describe(plans):
    out = []
    for p in plans:
        extra = ""
        if p["tipo"] == "quadros":
            extra = f" {len(p['tiles'])} quadros, {len(p['shots'])} plano(s)"
        elif p["tipo"] == "pessoa":
            extra = f" {len(p['shots'])} enquadramento(s)"
        elif p["tipo"] == "lateral":
            extra = f" {len(p['pequenos'])} câmeras à {p['lado']}, área grande = {'câmera' if p.get('rosto') else 'apresentação'}"
        out.append(f"{p['t0']:.1f}s {p['tipo']}{extra}")
    return " | ".join(out)


def info_video(path):
    cap = cv2.VideoCapture(path)
    fps, n = cap.get(cv2.CAP_PROP_FPS) or 30, int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps, n


def montar(cut, final_folder, cfg, plans):
    """Renderiza cuts/<base>_original_scale.mp4 com os planos (detectados ou editados) em final/<base>.mp4,
    mais <base>_layout.json e <base>_timeline.json (posição da legenda por trecho, lido pelo adjust_subtitles)."""
    base = os.path.basename(cut).replace("_original_scale.mp4", "")
    fps, n_frames = info_video(cut)
    plans = dividir_longos(dividir_por_zoom(plans, cfg))  # _layout.json sai dividido: o XML do Premiere monta igual
    try:
        render(cut, os.path.join(final_folder, f"{base}.mp4"), fps, n_frames, plans, cfg)
    except subprocess.CalledProcessError as e:  # um corte com problema não derruba os outros
        print(f"[Layout do canal] ERRO ao montar {base}: {e}")
        return False
    with open(os.path.join(final_folder, f"{base}_layout.json"), "w", encoding="utf-8") as f:
        json.dump(plans, f, ensure_ascii=False, indent=1)
    timeline = timeline_legenda(plans, cfg)
    caminho = os.path.join(final_folder, f"{base}_timeline.json")
    if timeline:
        with open(caminho, "w") as f:
            json.dump(timeline, f)
    elif os.path.exists(caminho):
        os.remove(caminho)
    return True


def timeline_legenda(plans, cfg):
    """Posição da legenda por trecho (lida pelo adjust_subtitles); trechos sem entrada usam a posição padrão."""
    return [{"start": p["t0"], "end": p["t1"], "mode": "1", "y": pos[0], "an": pos[1]}
            for p in plans for pos in [subtitle_pos(p, cfg)] if pos is not None]


def plano_em(plans, t):
    """Plano ativo no instante t do corte, já com o zoom fixo em quem fala naquele instante (para prévia)."""
    p = next((p for p in reversed(plans) if p["t0"] <= t), plans[0])
    p = dict(p)
    if p["tipo"] in ("quadros", "pessoa"):
        rel = t - p["t0"]
        p["shots"] = [(0.0, next((q for s, q in reversed(p["shots"]) if s <= rel), p["shots"][0][1]))]
    return p


def quadro(cut, plans, t, cfg, largura=360):
    """JPEG da montagem vertical no instante t (prévia da etapa de layouts)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", cut, "-frames:v", "1",
                        "-filter_complex", build_filter(plano_em(plans, t), cfg), "-map", "[v]",
                        "-s", f"{largura}x{largura * OUT_H // OUT_W}", "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                       capture_output=True, check=True)
    return r.stdout


def preview(video_path, seconds, layout_path=None):
    """Mostra o layout detectado num instante e gera preview.jpg com a montagem."""
    cfg = load_layout(layout_path)
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    plan = parse_frame(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if plan["tipo"] == "quadros":
        plan["shots"] = [(0.0, 0)]
    print(json.dumps(plan, ensure_ascii=False))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(seconds), "-i", video_path, "-frames:v", "1",
                    "-filter_complex", build_filter(plan, cfg), "-map", "[v]", "preview.jpg"], check=True)
    print("gerado: preview.jpg")


# ---------------------------------------------------------------- testes

def _synthetic(rects, W=1920, H=1080):
    img = np.full((H, W), 40, np.uint8)
    for x, y, w, h in rects:
        img[y:y + h, x:x + w] = 200
    return img


def selftest():
    # Detecção de layout em quadros sintéticos com a geometria real do canal
    p = parse_frame(_synthetic([(0, 0, 636, 1080), (642, 0, 636, 1080), (1286, 0, 634, 1080)]))
    assert p["tipo"] == "quadros" and len(p["tiles"]) == 3 and abs(p["tiles"][1][0] - 642) <= 2, p
    p = parse_frame(_synthetic([(0, 0, 956, 1080), (964, 0, 956, 1080)]))
    assert p["tipo"] == "quadros" and len(p["tiles"]) == 2, p
    apres = [(10, 52, 466, 315), (10, 372, 466, 315), (10, 692, 466, 316), (493, 49, 1354, 764)]
    p = parse_frame(_synthetic(apres))
    assert p["tipo"] == "lateral" and p["lado"] == "esq" and len(p["pequenos"]) == 3, p
    # borda da área grande fraca (escuro sobre escuro) e conteúdo compartilhado com borda forte mais adiante:
    # a área começa colada na coluna, não na borda do conteúdo
    g = _synthetic([r for r in apres[:3]])
    g[49:813, 493:1847] = 44            # área grande quase da cor do fundo
    g[100:700, 640:1500] = 200          # site/vídeo compartilhado, centralizado dentro dela
    p = parse_frame(g)
    assert p["tipo"] == "lateral" and abs(p["grande"][0] - 493) <= 3, p
    # borda de baixo da PRIMEIRA câmera some: o espaçamento sai do topo da segunda, continuam 3 câmeras
    g = _synthetic(apres)
    g[252:367, 10:476] = np.linspace(200, 44, 115).astype(np.uint8)[:, None]  # roupa escura: some em degradê
    p = parse_frame(g)
    assert p["tipo"] == "lateral" and len(p["pequenos"]) == 3 and abs(p["pequenos"][0][3] - 315) <= 4, p
    # linha forte dentro da primeira câmera não muda o espaçamento
    g = _synthetic(apres)
    g[252:367, 10:476] = 44
    g[367:372, 10:476] = 40
    p = parse_frame(g)
    assert p["tipo"] == "lateral" and len(p["pequenos"]) == 3, p
    # borda de baixo da câmera do meio some (roupa da cor da moldura): continuam 3 câmeras
    g = _synthetic(apres)
    g[692 - 40:692, 10:476] = 42
    p = parse_frame(g)
    assert p["tipo"] == "lateral" and len(p["pequenos"]) == 3, p
    assert abs(p["pequenos"][1][1] - 372) <= 2 and abs(p["grande"][0] - 493) <= 2, p
    conv = [(10, 115, 1506, 847)] + [(1526, 102 + k * 230, 383, 224) for k in range(4)]
    p = parse_frame(_synthetic(conv))
    assert p["tipo"] == "lateral" and p["lado"] == "dir" and len(p["pequenos"]) == 4, p
    assert 115 <= p["grande"][1] <= 120, p  # começa no topo real da câmera, não na faixa escura acima
    assert parse_frame(_synthetic([]))["tipo"] == "outro"
    # Bloco de 1 s: troca de layout achada por busca binária, lendo poucos quadros
    q3 = cv2.cvtColor(_synthetic([(0, 0, 636, 1080), (642, 0, 636, 1080), (1286, 0, 634, 1080)]), cv2.COLOR_GRAY2BGR)
    la = cv2.cvtColor(_synthetic(apres), cv2.COLOR_GRAY2BGR)
    ks = [layout_key(l) for l in _layouts_do_bloco([q3] * 3 + [la] * 5, parse_frame(q3[:, :, 0]))]
    assert ks == ["quadros3"] * 3 + [layout_key(parse_frame(la[:, :, 0]))] * 5, ks
    vazio = cv2.cvtColor(_synthetic([]), cv2.COLOR_GRAY2BGR)
    ks = [layout_key(l) for l in _layouts_do_bloco([la] * 2 + [q3] * 3 + [vazio] * 3, None)]  # duas trocas no bloco
    assert ks[2:5] == ["quadros3"] * 3 and ks[5:] == ["outro"] * 3 and ks[0].startswith("lateral"), ks

    # Suavização: piscada de 0,5 s de outro layout é absorvida
    times = [i / 8 for i in range(80)]
    keys = ["quadros3"] * 40 + ["outro"] * 4 + ["quadros3"] * 6 + ["lateral-esq-3-30"] * 30
    runs = smooth_runs(times, keys, window=8, min_run=1.5)
    assert [k for _, _, k in runs] == ["quadros3", "lateral-esq-3-30"], runs
    # 'outro' de 2,5 s no meio do mesmo layout (borda perdida) some; 'outro' longo fica
    keys = ["lateral-dir-2-0"] * 30 + ["outro"] * 20 + ["lateral-dir-2-0"] * 30
    assert [k for _, _, k in smooth_runs(times, keys, window=8, min_run=1.5)] == ["lateral-dir-2-0"]
    keys = ["lateral-dir-2-0"] * 20 + ["outro"] * 60
    assert [k for _, _, k in smooth_runs(times, keys, window=8, min_run=1.5)] == ["lateral-dir-2-0", "outro"]
    # Piscada curta absorvida pelo trecho anterior não pode deixar dois trechos iguais seguidos
    keys = ["lateral-dir-2-0"] * 30 + ["outro"] * 10 + ["lateral-dir-2-0"] * 40
    assert [k for _, _, k in smooth_runs(times, keys, window=2, min_run=1.5, outro_gap=0.5)] == ["lateral-dir-2-0"]

    # Quem fala
    act = [[0.02, 0, 0] if t < 4 else [0, 0, 0.02] for t in times]
    for i, t in enumerate(times):
        if 6.0 <= t < 6.3:
            act[i][1] = 0.05  # interjeição curta: ignorada
    shots = pick_speakers(times, act, min_shot=2.0, hold=0.6, min_activity=0.004)
    assert [q for _, q in shots] == [0, 2] and abs(shots[1][0] - 4.0) < 0.2, shots
    assert pick_speakers(times, [[0, 0, 0]] * 80) == [(0.0, 0)]
    assert crop_x_expr([(0.0, 0), (4.0, 2)], [0, 642, 1286]) == "if(lt(t,4.000),0,1286)"
    # Zoom afastado para quem está perto da câmera: o trecho se divide onde a fala passa de/para essa pessoa
    cfg_t = {"quadros_rosto_grande": 460}
    q = {"tipo": "quadros", "t0": 10.0, "t1": 30.0, "alturas": [420, 490, 380], "shots": [[0.0, 0], [5.0, 1], [9.0, 2], [12.0, 0]]}
    d = dividir_por_zoom([q], cfg_t)
    assert [(x["t0"], x["t1"], x["shots"]) for x in d] == [(10.0, 15.0, [[0.0, 0]]), (15.0, 19.0, [[0.0, 1]]),
                                                          (19.0, 30.0, [[0.0, 2], [3.0, 0]])], d
    assert dividir_por_zoom([dict(q, alturas=[420, 400, 380])], cfg_t) == [dict(q, alturas=[420, 400, 380])]
    assert [perto(dict(q, alturas=[465, 490, 380]), i, cfg_t) for i in range(3)] == [False, True, False]  # só o maior
    # Prévia: plano ativo no instante e o zoom em quem fala naquele instante
    planos = [{"tipo": "quadros", "t0": 0.0, "shots": [(0.0, 0), (4.0, 2)]}, {"tipo": "outro", "t0": 10.0}]
    assert plano_em(planos, 5.0)["shots"] == [(0.0, 2)] and plano_em(planos, 1.0)["shots"] == [(0.0, 0)]
    assert plano_em(planos, 12.0)["tipo"] == "outro"

    # Tela cheia em qualquer vídeo: o recorte segue quem aparece
    cfg_s = {"sample_fps": 8}
    rosto = lambda cx, mar=0.1, h=200, cy=400: ([cx - h / 2, cy - h / 2, cx + h / 2, cy + h / 2], mar)
    fala = lambda i: 0.1 + 0.2 * (i % 2)          # boca abrindo e fechando
    t80 = [i / 8 for i in range(80)]
    assert parse_frame(_synthetic([], W=1280, H=720)) == {"tipo": "outro", "w": 1280, "h": 720}  # canal só em 1080p
    assert seguir_rostos(t80, [[]] * 80, 1920, 1080, cfg_s) == [(0.0, None)]                    # ninguém: quadro inteiro
    assert seguir_rostos(t80, [[rosto(500)]] * 80, 1920, 1080, cfg_s) == [(0.0, 500.0)]         # uma pessoa parada
    # podcast: duas pessoas, fala a da esquerda e depois a da direita -> troca perto dos 5 s
    m = seguir_rostos(t80, [[rosto(500, fala(i) if i < 40 else 0.1), rosto(1400, fala(i) if i >= 40 else 0.1)] for i in range(80)], 1920, 1080, cfg_s)
    assert [c for _, c in m] == [500.0, 1400.0] and 4.5 <= m[1][0] <= 6.0, m
    # corte de cena (a pessoa some e aparece outra em outro lugar): troca na hora, sem esperar o plano mínimo
    m = seguir_rostos(t80, [[rosto(500)] if i < 12 else [rosto(1500)] for i in range(80)], 1920, 1080, cfg_s)
    assert [c for _, c in m] == [500.0, 1500.0] and abs(m[1][0] - 1.5) < 0.01, m
    # a pessoa anda para fora do recorte: reenquadra; balançar dentro da folga não gera troca
    m = seguir_rostos(t80, [[rosto(500 + min(i, 40) * 15)] for i in range(80)], 1920, 1080, cfg_s)
    assert len(m) >= 2 and abs(m[-1][1] - 1100) < 100, m
    assert len(seguir_rostos(t80, [[rosto(500 + 40 * (i % 2))] for i in range(80)], 1920, 1080, cfg_s)) == 1
    # a pessoa sai de cena por mais de 1,5 s: quadro inteiro a partir de onde ela sumiu; piscada do detector não conta
    m = seguir_rostos(t80, [[rosto(500)] if i < 24 else [] for i in range(80)], 1920, 1080, cfg_s)
    assert m == [(0.0, 500.0), (3.0, None)], m
    assert seguir_rostos(t80, [[] if 24 <= i < 30 else [rosto(500)] for i in range(80)], 1920, 1080, cfg_s) == [(0.0, 500.0)]
    # rosto pequeno no fundo e detecção falsa de 2 amostras são ignorados
    m = seguir_rostos(t80, [[rosto(500), rosto(1500, fala(i), h=50)] + ([rosto(900, 0.9)] if i in (20, 21) else []) for i in range(80)], 1920, 1080, cfg_s)
    assert m == [(0.0, 500.0)], m
    # planos: pessoa (com as trocas relativas ao início dela) e outro; montagem = recorte 608x1080 na tela toda
    ps = planos_tela_cheia(t80, [[rosto(500)] if i < 24 else [] for i in range(80)], 10.0, 20.0, 1920, 1080, cfg_s)
    assert [(p["tipo"], p["t0"], p["t1"]) for p in ps] == [("pessoa", 10.0, 13.0), ("outro", 13.0, 20.0)], ps
    assert planos_tela_cheia(t80, [[rosto(500)]] * 80, 0.0, 10.0, 1920, 1080, {"seguir_rostos": False})[0]["tipo"] == "outro"
    l = layers({"tipo": "pessoa", "w": 1920, "h": 1080, "shots": [[0.0, 100.0], [4.0, 1400.0]]}, {})
    assert l[0]["src"] == (0, 0, 606, 1080) and l[0]["trocas"] == [(0.0, 0), (4.0, 1097)] and l[0]["dst"] == (0, 0, 1080, 1920), l
    assert layers({"tipo": "pessoa", "w": 608, "h": 1080, "shots": [[0.0, 300.0]]}, {})[0]["src"] == (0, 0, 606, 1080)  # já vertical
    # análise completa de um vídeo comum (720p): planos com o tamanho real do vídeo
    lay = {"tipo": "outro", "w": 1280, "h": 720}
    ps = planejar(t80, [lay] * 80, [[rosto(300, h=150)]] * 80, 10.0, cfg_s)
    assert ps == [{"tipo": "pessoa", "w": 1280, "h": 720, "t0": 0.0, "t1": 10.0, "shots": [[0.0, 300.0]]}], ps
    longo = {"tipo": "pessoa", "t0": 5.0, "t1": 105.0, "shots": [[float(k), k] for k in range(100)]}
    d = dividir_longos([longo])
    assert [(p["t0"], p["t1"], len(p["shots"]), p["shots"][0]) for p in d] == [(5.0, 45.0, 40, [0.0, 0]), (45.0, 85.0, 40, [0.0, 40]), (85.0, 105.0, 20, [0.0, 80])], d
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        selftest()
    elif len(sys.argv) >= 4 and sys.argv[1] == "preview":
        preview(sys.argv[2], float(sys.argv[3]))
    else:
        print(__doc__)
