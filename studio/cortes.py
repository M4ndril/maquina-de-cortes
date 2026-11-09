"""
Cortador do Estúdio: um corte = lista de trechos [início, fim] da live colados em sequência.
Assunto normal = 1 trecho. Assunto resumido = frases escolhidas, sem as pausas longas dentro delas.

Gera, como o pipeline espera: cuts/<base>_original_scale.mp4 e subs/<base>_processed.json (tempos do corte).
"""
import functools
import json
import math
import os
import re
import subprocess

PAUSA_MAX = 0.5   # pausa entre palavras acima disso é cortada no resumo
RESPIRO = 0.12    # margem antes/depois de cada trecho para não cortar sílaba


def encaixar_assuntos(propostos, segmentos, maximo, folga=(0.2, 0.4), ocupados=()):
    """Assuntos do Claude ({titulo, inicio, fim, nota...}) com início/fim encaixados na frase mais próxima,
    sem sobreposição (vale o de nota maior) e no máximo 'maximo'. ocupados: [(início, fim)] que já são assuntos."""
    inicios, fins = [s["start"] for s in segmentos], [s["end"] for s in segmentos]
    out = []
    for a in sorted(propostos, key=lambda a: -float(a.get("nota") or 0)):
        ini = min(inicios, key=lambda x: abs(x - float(a["inicio"]))) - folga[0]
        fim = min(fins, key=lambda x: abs(x - float(a["fim"]))) + folga[1]
        ini = max(0.0, ini)
        if fim - ini < 1 or any(ini < o["fim"] and fim > o["inicio"] for o in out) or any(ini < f and fim > i for i, f in ocupados):
            continue
        out.append(dict(a, inicio=round(ini, 2), fim=round(fim, 2)))
        if len(out) >= maximo:
            break
    return sorted(out, key=lambda a: a["inicio"])


def nome_base(i, titulo):
    """i = número do assunto (3 -> "003") ou rótulo pronto ("003-2" para um corte dentro do assunto 3).
    Título curto (40): a pasta da entrega entra no caminho de todos os arquivos (Windows/Google Drive: 260)."""
    seguro = "".join(c for c in titulo if c.isalnum() or c in " _-").strip().replace(" ", "_")[:40].strip("_")
    return f"{i:03d}_{seguro or 'corte'}" if isinstance(i, int) else f"{i}_{seguro or 'corte'}"


def numero(assuntos, a):
    """Rótulo do assunto na lista: assuntos principais 1, 2, 3...; cortes dentro de um assunto 3-1, 3-2..."""
    topo = [x for x in assuntos if not x.get("pai")]
    if not a.get("pai"):
        return f"{topo.index(a) + 1:03d}"
    pai = next((x for x in topo if x["id"] == a["pai"]), None)
    irmaos = [x for x in assuntos if x.get("pai") == a["pai"]]
    return f"{topo.index(pai) + 1 if pai else 0:03d}-{irmaos.index(a) + 1}"


def carregar_transcricao(pasta, corrigida=True):
    """Frases da live; corrigida = com as correções de texto do usuário (projeto.json "correcoes") aplicadas."""
    with open(os.path.join(pasta, "input.json"), encoding="utf-8") as f:
        segs = json.load(f)["segments"]
    proj = os.path.join(pasta, "projeto.json")
    if corrigida and os.path.exists(proj):
        with open(proj, encoding="utf-8") as f:
            aplicar_correcoes(segs, json.load(f).get("correcoes") or {})
    return segs


def regra_troca(de):
    """Palavra inteira, sem diferenciar maiúsculas ("flavio" acha "Flavio," mas não "Flavios")."""
    return re.compile(rf"(?<!\w){re.escape(de.strip())}(?!\w)", re.IGNORECASE)


def aplicar_correcoes(segs, corr):
    """corr = {"trocas": [{"de", "para"}] (toda a live), "palavras": {"<frase>:<palavra>": texto}} (uma palavra;
    vale sobre as trocas; texto vazio tira a palavra da legenda). Palavra mudada guarda o texto em "original"."""
    trocas = [(regra_troca(t["de"]), t["para"]) for t in corr.get("trocas", []) if t.get("de", "").strip()]
    palavras = corr.get("palavras", {})
    if not trocas and not palavras:
        return segs
    for si, s in enumerate(segs):
        mudou = False
        for wi, w in enumerate(s.get("words", [])):
            novo = w["word"]
            for rx, para in trocas:
                novo = rx.sub(lambda _: para, novo)
            novo = palavras.get(f"{si}:{wi}", novo)
            if novo != w["word"]:
                w["original"], w["word"], mudou = w["word"], novo, True
        if mudou:
            s["text"] = " ".join(w["word"].strip() for w in s["words"] if w["word"].strip())
    return segs


def tem_numero(palavra):
    """Algarismos ("50,86%", "2022") não são alinhados pela voz: o tempo dessas palavras e das vizinhas não é confiável."""
    return any(c.isdigit() for c in palavra)


def trechos_do_resumo(frases_idx, segmentos):
    """Frases mantidas -> trechos, cortando pausas > PAUSA_MAX entre palavras e juntando o que ficar colado."""
    trechos = []
    for i in sorted(set(frases_idx)):
        if not 0 <= i < len(segmentos):
            continue
        palavras = [w for w in segmentos[i].get("words", []) if "start" in w and "end" in w]
        if not palavras:
            trechos.append([segmentos[i]["start"], segmentos[i]["end"]])
            continue
        ini = palavras[0]["start"]
        com_numero = any(tem_numero(w["word"]) for w in palavras)  # "pausas" nessa frase podem ser fala
        for a, b in zip(palavras, palavras[1:]):
            if not com_numero and b["start"] - a["end"] > PAUSA_MAX:
                trechos.append([ini, a["end"]])
                ini = b["start"]
        trechos.append([ini, palavras[-1]["end"]])
    trechos = [[max(0.0, s - RESPIRO), e + RESPIRO] for s, e in trechos]
    unidos = []
    for s, e in sorted(trechos):
        if unidos and s <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], e)
        else:
            unidos.append([s, e])
    return unidos


def trechos_do_assunto(assunto, segmentos):
    """Resumo = só as frases escolhidas (sem as pausas longas). Senão o assunto inteiro, menos as frases
    desligadas pelo usuário: sai a frase e a pausa até a vizinha (fica um respiro), o resto continua natural."""
    if assunto.get("resumo"):
        return trechos_do_resumo(assunto["resumo"]["frases"], segmentos)
    trechos = [[float(assunto["inicio"]), float(assunto["fim"])]]
    for k in sorted(set(assunto.get("desligadas") or [])):
        if not 0 <= k < len(segmentos):
            continue
        s = segmentos[k - 1]["end"] + RESPIRO if k > 0 else segmentos[k]["start"]
        e = segmentos[k + 1]["start"] - RESPIRO if k + 1 < len(segmentos) else segmentos[k]["end"]
        s, e = min(s, segmentos[k]["start"]), max(e, segmentos[k]["end"])  # a frase sai inteira
        trechos = [p for a, b in trechos for p in ([a, min(b, s)], [max(a, e), b]) if p[1] - p[0] >= 0.3]
    return trechos


# ---- bordas no silêncio: o tempo das palavras (alinhamento) costuma marcar o início um pouco depois da voz
# começar e o fim um pouco antes de acabar; cortar nele come sílabas. Cada borda vai até o silêncio mais próximo.
QUADRO = 0.01        # 10 ms por medida de volume
SILENCIO = 10        # 100 ms perto do ruído de fundo = silêncio de verdade (oclusivas no meio da fala são mais curtas)
ALCANCE = 0.6        # quanto a borda pode andar procurando o silêncio
ALCANCE_NUMERO = 3.0  # trecho com algarismos: o tempo das palavras pode estar segundos adiantado ("50,86%" = 2 s de fala)
DENTRO = 0.05        # ponto de partida: um pouco para dentro da fala
MARGEM = (0.08, 0.12)  # folga depois de achar o silêncio (antes do início, depois do fim): sobrar respiro > comer sílaba


def borda_no_silencio(rms, i_ref, direcao, limiar, alcance=ALCANCE):
    """Índice do quadro onde a fala começa (direcao=-1, procurando para trás a partir de i_ref)
    ou termina (direcao=+1, para frente). None se não houver silêncio dentro do alcance."""
    quieto = rms < limiar
    passos = int(alcance / QUADRO)
    for d in range(passos):
        i = i_ref + direcao * d
        a, b = (i - SILENCIO + 1, i + 1) if direcao < 0 else (i, i + SILENCIO)
        if a < 0 or b > len(rms):
            return None
        janela = quieto[a:b]
        if janela.all():
            return i + 1 if direcao < 0 else i
    return None


def _volume(video, t0, dur):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{dur:.3f}", "-i", video, "-vn",
                        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768
    n = len(x) // 160
    return np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(axis=1)) if n else np.zeros(0)


def ajustar_ao_silencio(video, trechos, incertos=()):
    """incertos: índices dos trechos com algarismos (tempo das palavras não confiável): procura o silêncio mais longe."""
    import numpy as np
    out = []
    for n, (s, e) in enumerate(trechos):
        novo = []
        alcance = ALCANCE_NUMERO if n in incertos else ALCANCE
        for t, direcao in ((s + RESPIRO + DENTRO, -1), (e - RESPIRO - DENTRO, +1)):
            meia = max(1.5, alcance + 0.3)
            ini = max(0.0, t - meia)
            rms = _volume(video, ini, 2 * meia)
            # limiar relativo à fala ao redor (-20 dB do pico), com piso absoluto para trechos muito baixos
            # silêncio = perto do ruído de fundo da sala. Comparar com o pico da fala tratava consoantes fracas
            # ("s", "f", "ch") como silêncio e comia o "s" de "votos"
            limiar = max(3 * float(np.percentile(rms, 5)), 0.01 * float(np.percentile(rms, 95)), 0.0005) if len(rms) else 1.0
            i = borda_no_silencio(rms, int((t - ini) / QUADRO), direcao, limiar, alcance)
            if i is None:
                # fala colada (sem silêncio por perto): corta no ponto de menor volume entre as palavras,
                # sem entrar na palavra mantida (início: até 20 ms depois dela começar; fim: a partir de 20 ms antes)
                palavra = s + RESPIRO if direcao < 0 else e - RESPIRO
                folga = alcance if n in incertos else 0.25
                a, b = (palavra - folga, palavra + 0.02) if direcao < 0 else (palavra - 0.02, palavra + folga)
                ia, ib = max(0, int((a - ini) / QUADRO)), min(len(rms), int((b - ini) / QUADRO))
                novo.append(ini + (ia + int(np.argmin(rms[ia:ib]))) * QUADRO if ib > ia else (s if direcao < 0 else e))
            else:
                novo.append(ini + i * QUADRO + (-MARGEM[0] if direcao < 0 else MARGEM[1]))
        novo[0] = min(novo[0], s + RESPIRO)        # nunca começar depois da primeira palavra
        novo[1] = max(novo[1], e - RESPIRO)        # nem terminar antes da última
        out.append(novo)
    unidos = []
    for s, e in sorted(out):
        if unidos and s <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], e)
        else:
            unidos.append([max(0.0, s), e])
    return unidos


def mapear_tempo(t, origem, destino):
    """Instante t num corte feito com os trechos 'origem' -> instante no corte feito com 'destino'
    (mesma fala, bordas um pouco diferentes). Cai num buraco entre trechos = vai para o começo do próximo."""
    acc, vivo = 0.0, origem[-1][1]
    for s, e in origem:
        if t <= acc + (e - s):
            vivo = s + (t - acc)
            break
        acc += e - s
    acc = 0.0
    for s, e in destino:
        if vivo < s:
            return acc
        if vivo <= e:
            return acc + vivo - s
        acc += e - s
    return acc


def remapear_planos(plans, origem, destino):
    """Planos de layout (t0 e trocas de zoom relativas) de um corte para o outro."""
    out = []
    for p in plans:
        q = dict(p, t0=mapear_tempo(p["t0"], origem, destino))
        if "t1" in p:
            q["t1"] = mapear_tempo(p["t1"], origem, destino)
        if p.get("shots"):
            q["shots"] = [[max(0.0, mapear_tempo(p["t0"] + s, origem, destino) - q["t0"]), k] for s, k in p["shots"]]
            q["shots"][0][0] = 0.0
        out.append(q)
    return _cobrir(out, destino)


def _cobrir(plans, trechos):
    """Planos cobrem o corte inteiro: o primeiro começa em 0 e o último vai até o fim."""
    if plans:
        d, plans[0]["t0"] = plans[0]["t0"], 0.0
        if plans[0].get("shots"):  # trocas de zoom são relativas ao início do plano: ficam no mesmo instante
            plans[0]["shots"] = plans[0]["shots"][:1] + [[t + d, k] for t, k in plans[0]["shots"][1:]]
        plans[-1]["t1"] = sum(e - s for s, e in trechos)
    return plans


def fatiar_planos(plans, x, y, destino):
    """Pedaço [x, y) de uma lista de planos (tempos do corte), deslocado para começar em 'destino'."""
    out = []
    for i, p in enumerate(plans):
        fim = plans[i + 1]["t0"] if i + 1 < len(plans) else math.inf
        a, b = max(p["t0"], x), min(fim, y)
        if b <= a:
            continue
        q = {k: v for k, v in p.items() if k != "shots"}
        q.update(t0=a - x + destino, t1=b - x + destino)
        if p.get("shots"):
            rel = a - p["t0"]
            atual = next(k for t, k in reversed(p["shots"]) if t <= rel + 1e-6)
            q["shots"] = [[0.0, atual]] + [[t - rel, k] for t, k in p["shots"] if rel + 1e-6 < t < b - p["t0"]]
        out.append(q)
    return out


def _juntar(a, b):
    """Dois planos seguidos do mesmo layout viram um; vale a geometria/escolha do que veio da análise antiga
    (é onde estão as correções do usuário)."""
    m = dict(b if b.get("_antigo") and not a.get("_antigo") else a, t0=a["t0"], t1=b["t1"])
    m["_antigo"] = a.get("_antigo") or b.get("_antigo")
    if a.get("shots") and b.get("shots"):
        shots = []
        for t, k in a["shots"] + [[t + b["t0"] - a["t0"], k] for t, k in b["shots"]]:
            if not shots or shots[-1][1] != k:
                shots.append([t, k])
        m["shots"] = shots
    return m


def combinar_planos(antigos, origem, novos, destino, chave, min_plano=1.0):
    """Planos para o corte 'destino' depois de uma edição do assunto: onde a fala já estava no corte antigo
    ('origem', com os planos 'antigos' já corrigidos) reaproveita esses planos; o resto vem da detecção nova
    ('novos', feita em 'destino'). chave(plano) = layout (planos seguidos com a mesma chave são unidos)."""
    pecas, acc = [], 0.0
    for s, e in destino:
        cobertos, acc_o = [], 0.0
        for os_, oe in origem:
            lo, hi = max(s, os_), min(e, oe)
            if hi > lo:
                cobertos.append((lo, hi, acc_o + lo - os_))
            acc_o += oe - os_
        cursor = s
        for lo, hi, x in cobertos:
            if lo > cursor:
                pecas += fatiar_planos(novos, acc + cursor - s, acc + lo - s, acc + cursor - s)
            pecas += [dict(p, _antigo=True) for p in fatiar_planos(antigos, x, x + hi - lo, acc + lo - s)]
            cursor = hi
        if e > cursor:
            pecas += fatiar_planos(novos, acc + cursor - s, acc + e - s, acc + cursor - s)
        acc += e - s
    out = []
    for p in pecas:
        if out and (p["t1"] - p["t0"] < min_plano or chave(out[-1]) == chave(p)):
            out[-1] = _juntar(out[-1], p) if chave(out[-1]) == chave(p) else dict(out[-1], t1=p["t1"])  # sobra curta: some
        else:
            out.append(p)
    if len(out) > 1 and out[0]["t1"] - out[0]["t0"] < min_plano:  # sobra curta no começo: o seguinte começa em 0
        d = out[1]["t0"]
        out[1] = dict(out[1], t0=0.0)
        if out[1].get("shots"):
            out[1]["shots"] = [out[1]["shots"][0]] + [[t + d, k] for t, k in out[1]["shots"][1:]]
        out.pop(0)
    for p in out:
        p.pop("_antigo", None)
    return _cobrir(out, destino)


def trechos_da_assinatura(assin):
    """(trechos sem ajuste, versão do cortador) guardados na assinatura de uma análise."""
    d = json.loads(assin)
    return (d, 1) if isinstance(d, list) else (d["trechos"], d["v"])


@functools.lru_cache
def fps_video(caminho):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                        "-of", "csv=p=0", caminho], capture_output=True, text=True).stdout.strip()
    n, _, d = r.partition("/")
    return float(n) / float(d or 1)


def na_grade(trechos, fps):
    """Bordas em quadros inteiros (início para trás, fim para frente): cada trecho do corte dura exatamente e - s,
    então o tempo da legenda e dos layouts (soma das durações) bate com o vídeo até o fim do corte."""
    out = []
    for s, e in trechos:
        s, e = math.floor(s * fps + 1e-6) / fps, math.ceil(e * fps - 1e-6) / fps
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def trechos_finais(pasta, assunto, segmentos):
    """Trechos que vão para o vídeo E para a legenda (bordas no silêncio). Determinístico: layouts e exportação batem."""
    trechos = trechos_do_assunto(assunto, segmentos)
    incertos = {n for n, (s, e) in enumerate(trechos)
                if any(tem_numero(w["word"]) for seg in segmentos if seg["end"] > s and seg["start"] < e
                       for w in seg.get("words", []) if s <= w.get("start", -1) <= e)}
    video = os.path.join(pasta, "input.mp4")
    return na_grade(ajustar_ao_silencio(video, trechos, incertos), fps_video(video))


def legenda_dos_trechos(segmentos, trechos):
    """Recorta a transcrição nos trechos e desloca os tempos para a linha do tempo do corte."""
    out, offset = [], 0.0
    for s, e in trechos:
        for si, seg in enumerate(segmentos):
            if seg["end"] <= s or seg["start"] >= e:
                continue
            # ref = "<frase>:<palavra>" na transcrição: é por ela que a correção de texto acha a palavra
            palavras = [dict(w, start=max(w["start"], s) - s + offset, end=min(w["end"], e) - s + offset, ref=f"{si}:{wi}")
                        for wi, w in enumerate(seg.get("words", []))
                        if "start" in w and w["end"] > s and w["start"] < e and w["word"].strip()]
            if not palavras:
                continue
            out.append({"start": palavras[0]["start"], "end": palavras[-1]["end"],
                        "text": " ".join(w["word"].strip() for w in palavras), "words": palavras})
        offset += e - s
    return {"segments": out}


def assinatura(trechos):
    """Identifica o recorte: análise de layout feita para outra assinatura está desatualizada."""
    # versão do cortador: v2 = bordas no silêncio; v5 = duração exata; vídeo cortado por outra versão não serve mais
    return json.dumps({"v": 5, "trechos": [[round(s, 2), round(e, 2)] for s, e in trechos]})


def cortar(pasta, i, assunto, segmentos, video_pronto=None, trechos_video=None):
    """video_pronto: vídeo do assunto já cortado (etapa de layouts), com os trechos_video exatos dele."""
    base = nome_base(i, assunto["titulo"])
    trechos = trechos_video or trechos_finais(pasta, assunto, segmentos)  # vídeo pronto: legenda nos trechos dele
    os.makedirs(os.path.join(pasta, "cuts"), exist_ok=True)
    os.makedirs(os.path.join(pasta, "subs"), exist_ok=True)
    destino = os.path.join(pasta, "cuts", f"{base}_original_scale.mp4")
    if video_pronto:
        if os.path.exists(destino):
            os.remove(destino)
        os.link(video_pronto, destino)
    else:
        cortar_video(pasta, trechos, destino)
    with open(os.path.join(pasta, "subs", f"{base}_processed.json"), "w", encoding="utf-8") as f:
        json.dump(legenda_dos_trechos(segmentos, trechos), f, ensure_ascii=False)
    return base, trechos


LOTE = 40  # trechos por comando do ffmpeg: o Windows limita a linha de comando a ~32 mil caracteres


def cortar_video(pasta, trechos, destino, encoder="h264_nvenc"):
    """Cola os trechos (bordas na grade de quadros, ver na_grade) com duração exata: sem isso cada emenda
    sobrava ~15 ms e a legenda de um resumo longo terminava quase 1 s adiantada.
    Resumo com muitos trechos: corta em lotes (áudio PCM, que emenda sem perder nenhuma amostra) e junta no fim."""
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    if len(trechos) <= LOTE:
        return _colar(pasta, trechos, destino, encoder, ["-c:a", "aac", "-b:a", "192k"])
    import tempfile
    with tempfile.TemporaryDirectory(dir=os.path.dirname(destino)) as tmp:
        partes = []
        for i in range(0, len(trechos), LOTE):
            partes.append(os.path.join(tmp, f"lote_{i // LOTE:03d}.mkv"))
            _colar(pasta, trechos[i:i + LOTE], partes[-1], encoder, ["-c:a", "pcm_s16le"])
        lista = os.path.join(tmp, "lista.txt")
        with open(lista, "w", encoding="utf-8") as f:
            f.writelines(f"file '{os.path.abspath(x)}'\n".replace("\\", "/") for x in partes)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", destino], check=True)


def _colar(pasta, trechos, destino, encoder, audio):
    entrada = os.path.join(pasta, "input.mp4")
    fps = fps_video(entrada)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filtros = []
    for k, (s, e) in enumerate(trechos):
        d = e - s
        folga = min(s, 0.25 / fps)  # entra um pouco antes: o quadro em s nunca fica de fora por arredondamento
        cmd += ["-ss", f"{s - folga:.6f}", "-t", f"{d + 0.5:.6f}", "-i", entrada]  # uma entrada por trecho: pula direto
        # fade de 10 ms em cada emenda: corte seco no áudio estala
        filtros.append(f"[{k}:v]trim=end_frame={round(d * fps)},setpts=PTS-STARTPTS[v{k}];"
                       f"[{k}:a]atrim=start={folga:.6f}:end={folga + d:.6f},asetpts=PTS-STARTPTS,"
                       f"afade=t=in:d=0.01,afade=t=out:st={max(0.0, d - 0.01):.4f}:d=0.01[a{k}]")
    filtros.append("".join(f"[v{k}][a{k}]" for k in range(len(trechos))) + f"concat=n={len(trechos)}:v=1:a=1[v][a]")
    cmd += ["-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "[a]",
            "-c:v", encoder, "-b:v", "10M", *audio, destino]
    subprocess.run(cmd, check=True)


def selftest():
    w = lambda t, s, e: {"word": t, "start": s, "end": e}
    segs = [
        {"start": 0.0, "end": 3.0, "words": [w("a", 0.0, 0.4), w("b", 0.5, 0.9), w("c", 2.0, 3.0)]},  # pausa de 1,1 s
        {"start": 3.1, "end": 4.0, "words": [w("d", 3.1, 4.0)]},
        {"start": 9.0, "end": 10.0, "words": [w("e", 9.0, 10.0)]},
    ]
    tr = trechos_do_resumo([0, 1, 2], segs)
    # frase 0 vira dois trechos (pausa cortada); frase 1 encosta no fim da 0 -> unidos; frase 2 separada
    assert [[round(a, 2), round(b, 2)] for a, b in tr] == [[0.0, 1.02], [1.88, 4.12], [8.88, 10.12]], tr
    leg = legenda_dos_trechos(segs, [[0.0, 1.0], [9.0, 10.0]])["segments"]
    assert [x["text"] for x in leg] == ["a b", "e"] and leg[1]["words"][0]["start"] == 1.0, leg
    assert trechos_do_assunto({"inicio": 5, "fim": 8}, segs) == [[5.0, 8.0]]
    # frase desligada: sai ela e a pausa até as vizinhas (fica o respiro); a 1ª desligada encurta o começo
    assert trechos_do_assunto({"inicio": 0, "fim": 10, "desligadas": [1]}, segs) == [[0.0, 3.1], [8.88, 10.0]]
    assert trechos_do_assunto({"inicio": 0, "fim": 4, "desligadas": [0]}, segs) == [[3.0, 4.0]]  # pausa menor que o respiro: começa onde a desligada acaba
    lista = [{"id": "a"}, {"id": "b", "pai": "a"}, {"id": "c"}, {"id": "d", "pai": "a"}]
    assert [numero(lista, x) for x in lista] == ["001", "001-1", "002", "001-2"]
    # assuntos do Claude: encaixe nas frases; o de nota menor que sobrepõe sai
    r = encaixar_assuntos([{"titulo": "x", "inicio": 2.9, "fim": 9.7, "nota": 50}, {"titulo": "y", "inicio": 0.1, "fim": 3.9, "nota": 90}], segs, 5)
    assert [(a["titulo"], a["inicio"], a["fim"]) for a in r] == [("y", 0.0, 4.4)], r
    r = encaixar_assuntos([{"titulo": "y", "inicio": 0.1, "fim": 3.9}], segs, 5, ocupados=[(2.0, 6.0)])
    assert r == [], r  # já existe um assunto ali: não entra de novo
    # correções: troca em toda a live (palavra inteira, com pontuação em volta) e edição de uma palavra
    cs = [{"start": 0, "end": 3, "text": "x", "words": [w("Flavio,", 0, 1), w("Flavios", 1, 2), w("tchau", 2, 3)]}]
    aplicar_correcoes(cs, {"trocas": [{"de": "flavio", "para": "Flávio"}], "palavras": {"0:2": ""}})
    assert [x["word"] for x in cs[0]["words"]] == ["Flávio,", "Flavios", ""] and cs[0]["text"] == "Flávio, Flavios"
    assert cs[0]["words"][0]["original"] == "Flavio," and "original" not in cs[0]["words"][1]
    assert [x["ref"] for x in legenda_dos_trechos(cs, [[0, 3]])["segments"][0]["words"]] == ["0:0", "0:1"]  # vazia sai
    # borda no silêncio: fala nos quadros 30-69, silêncio em volta
    import numpy as np
    rms = np.full(120, 0.001); rms[30:70] = 0.2
    assert borda_no_silencio(rms, 40, -1, 0.02) == 30      # início: volta até onde a voz começa
    assert borda_no_silencio(rms, 60, +1, 0.02) == 70      # fim: avança até a voz acabar
    rms[:] = 0.2                                           # fala colada, sem silêncio por perto
    assert borda_no_silencio(rms, 60, -1, 0.02) is None and borda_no_silencio(rms, 5, -1, 0.02) is None
    # frase com algarismos não é picotada nas "pausas" (o tempo das palavras ao redor do número é falso)
    num = [{"start": 0.0, "end": 5.0, "words": [w("a", 0.0, 0.4), w("50,86%", 0.5, 0.9), w("dos", 2.0, 3.0)]}]
    assert len(trechos_do_resumo([0], num)) == 1 and tem_numero("50,86%") and not tem_numero("votos")
    assert na_grade([[1.01, 2.0], [2.02, 3.0]], 30) == [[1.0, 3.0]]  # bordas para fora; encostou = um trecho só
    # edição do assunto: correções valem onde a fala continua; o trecho novo vem da detecção
    chave = lambda p: p["tipo"]
    antigos = [{"tipo": "quadros", "t0": 0.0, "t1": 6.0, "shots": [[0.0, 1], [4.0, 2]]},   # corrigido pelo usuário
               {"tipo": "outro", "t0": 6.0, "t1": 10.0}]
    novos = [{"tipo": "lateral", "t0": 0.0, "t1": 3.0, "rosto": [1, 1]},                    # 3 s acrescentados antes
             {"tipo": "quadros", "t0": 3.0, "t1": 9.0, "shots": [[0.0, 0]]},
             {"tipo": "outro", "t0": 9.0, "t1": 11.0}]
    r = combinar_planos(antigos, [[100.0, 110.0]], novos, [[97.0, 106.0], [108.0, 110.0]], chave)
    assert [(p["tipo"], p["t0"], p["t1"]) for p in r] == [("lateral", 0.0, 3.0), ("quadros", 3.0, 9.0), ("outro", 9.0, 11.0)], r
    assert r[1]["shots"] == [[0.0, 1], [4.0, 2]], r[1]   # zoom corrigido continua, no tempo novo
    assert fatiar_planos(antigos, 5.0, 7.0, 0.0)[0]["shots"] == [[0.0, 2]]
    r = remapear_planos(antigos, [[100.0, 110.0]], [[100.3, 110.5]])  # bordas mudaram: ainda cobre o corte todo
    assert r[0]["t0"] == 0.0 and abs(r[-1]["t1"] - 10.2) < 1e-9, r
    rms = np.full(400, 0.001); rms[30:250] = 0.2           # fala até 2,5 s: alcance normal não acha o fim
    assert borda_no_silencio(rms, 60, +1, 0.02) is None and borda_no_silencio(rms, 60, +1, 0.02, 3.0) == 250
    # conversão de tempos entre cortes do mesmo conteúdo com bordas diferentes
    velho, novo = [[10.0, 20.0], [30.0, 40.0]], [[9.5, 20.5], [30.2, 40.0]]
    assert mapear_tempo(0.0, velho, novo) == 0.5 and abs(mapear_tempo(2.0, velho, novo) - 2.5) < 1e-9  # 1º trecho
    assert abs(mapear_tempo(12.0, velho, novo) - 12.8) < 1e-9          # 2º trecho: 32 s da live = 11 + 1,8
    p = remapear_planos([{"t0": 0.0, "shots": [[0.0, 1], [12.0, 2]]}], velho, novo)[0]
    assert p["t0"] == 0.0 and p["shots"][0] == [0.0, 1] and abs(p["shots"][1][0] - 12.8) < 1e-9, p  # troca no mesmo instante
    assert trechos_da_assinatura("[[1, 2]]") == ([[1, 2]], 1) and trechos_da_assinatura(assinatura([[1, 2]]))[1] == 5
    assert nome_base(3, "Olá, mundo! É isso?") == "003_Olá_mundo_É_isso" and nome_base("003-2", "x") == "003-2_x"
    print("selftest ok")


if __name__ == "__main__":
    selftest()
