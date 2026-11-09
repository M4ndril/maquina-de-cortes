// Etapa 3: conferir e corrigir o layout de cada corte (o que a montagem vertical mostra a cada instante).
// Usa h(), api(), fmt(), dur(), enc(), projeto, ocupado(), tarefa de app.js.

const CORES_PESSOA = ["#ff5a36", "#3c9cff", "#3ccf8e", "#ffb347"];
const NOME_TIPO = { quadros: "Quadros lado a lado", lateral: "Câmeras laterais", pessoa: "Seguindo quem aparece", outro: "Tela cheia" };
let selLayout = null;

function etapaLayouts() {
  const incluidos = projeto.assuntos.filter((a) => a.incluir);
  const estado = (a) => (!a.layout ? "não analisado" : a.layout_estado === "ok" ? "ok" : "desatualizado");
  const pendentes = incluidos.filter((a) => estado(a) !== "ok");
  if (!incluidos.some((a) => a.id === selLayout)) selLayout = incluidos.find((a) => a.layout)?.id || incluidos[0]?.id;

  const analisar = (lista) => api("POST", `/api/projetos/${enc(projeto.nome)}/layouts`, { ids: lista.map((a) => a.id) });
  const lista = h("div", { class: "lista-assuntos" }, incluidos.map((a) =>
    h("div", { class: "item-assunto" + (a.id === selLayout ? " sel" : "") + (a.pai ? " filho" : ""), onclick: () => { selLayout = a.id; rotear(); } },
      h("span", { class: "fraco" }, `${numeroAssunto(a)}.`), h("span", { class: "nome" }, a.titulo),
      h("span", { class: "selo" + (estado(a) === "ok" ? " ok" : "") }, estado(a)))));

  const a = projeto.assuntos.find((x) => x.id === selLayout);
  return h("div", { class: "layouts" },
    h("div", { class: "cartao" },
      h("div", { class: "linha" },
        h("button", { class: "primario", disabled: ocupado() || !pendentes.length, onclick: () => analisar(pendentes) },
          ocupado() && tarefa.tipo === "layouts" ? "Analisando..." : pendentes.length ? `Analisar layouts (${pendentes.length})` : "Layouts em dia"),
        h("span", { class: "fraco pequeno", style: "flex:1" },
          "Cada assunto é cortado e analisado: layout da live a cada instante e quem fala. Mudou início, fim ou resumo? Analise de novo: só o trecho novo é analisado e suas correções continuam onde a fala continua."),
        h("a", { href: `#/p/${enc(projeto.nome)}/4` }, h("button", {}, "B-roll →")),
        h("a", { href: `#/p/${enc(projeto.nome)}/5` }, h("button", { class: "primario" }, "Exportar →")))),
    h("div", { class: "layouts-grade" },
      h("div", { class: "cartao" }, lista),
      a?.layout ? editorLayout(a, () => analisar([a])) :
        h("div", { class: "cartao fraco" }, a ? "Este assunto ainda não foi analisado." : "Nenhum assunto incluído na exportação.")));
}

function editorLayout(a, reanalisar) {
  const plans = a.layout.plans;
  const desatualizado = a.layout_estado !== "ok";
  const video = h("video", { controls: true, preload: "auto", src: `/media/${enc(projeto.nome)}/layouts/${a.id}.mp4?v=${enc(a.layout.assinatura.slice(-24))}` });  // muda quando o recorte muda
  const previa = h("img", { class: "previa-vertical", alt: "Prévia da montagem vertical" });
  const faixa = h("div", { class: "faixa-layout" });
  const painel = h("div", { class: "painel-trecho" });
  const duracao = () => video.duration || plans[plans.length - 1].t1 || 1;

  // ---------------------------------------------------------- estado / salvar
  let salvarT = null, previaT = null, previaURL = null;
  const salvar = () => {
    clearTimeout(salvarT);
    salvarT = setTimeout(() => api("PUT", `/api/projetos/${enc(projeto.nome)}/assuntos/${a.id}/layout`, { plans }), 300);
  };
  const atualizarPrevia = () => {
    clearTimeout(previaT);
    previaT = setTimeout(async () => {
      const r = await fetch(`/api/projetos/${enc(projeto.nome)}/assuntos/${a.id}/quadro`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: video.currentTime, plans }) });
      if (!r.ok) return;
      if (previaURL) URL.revokeObjectURL(previaURL);
      previa.src = previaURL = URL.createObjectURL(await r.blob());
    }, 150);
  };
  const alterado = () => { salvar(); desenhar(); atualizarPrevia(); };

  // ---------------------------------------------------------- consultas no instante atual
  const indiceEm = (t) => Math.max(0, plans.reduce((k, p, i) => (p.t0 <= t ? i : k), 0));
  const shotEm = (p, rel) => Math.max(0, p.shots.reduce((k, [s], i) => (s <= rel + 1e-6 ? i : k), 0));

  // ---------------------------------------------------------- correções
  const comPlano = (fn) => () => { const p = plans[indiceEm(video.currentTime)]; fn(p, video.currentTime - p.t0); alterado(); };
  const zoomEm = (pessoa) => comPlano((p, rel) => { p.shots[shotEm(p, rel)][1] = pessoa; });
  const trocarAqui = comPlano((p, rel) => {
    const k = shotEm(p, rel);
    if (rel - p.shots[k][0] < 0.5) return;           // já existe troca neste ponto
    p.shots.splice(k + 1, 0, [+rel.toFixed(2), p.shots[k][1]]);
  });
  const removerTroca = comPlano((p, rel) => { const k = shotEm(p, rel); if (k > 0) p.shots.splice(k, 1); });
  const areaGrande = (camera) => comPlano((p) => {
    if (camera && !p.rosto) { const [gx, gy, gw, gh] = p.grande; p.rosto = [gx + gw / 2, gy + gh * 0.4]; p.rosto_manual = true; }
    if (camera && p.rosto_guardado) { p.rosto = p.rosto_guardado; delete p.rosto_guardado; }
    if (!camera && p.rosto) { p.rosto_guardado = p.rosto; delete p.rosto; }
  });
  // trecho "pessoa": o valor de cada troca é o centro x do recorte vertical no vídeo original
  const larguraVideo = (p) => p.w || 1920;
  const moverRecorte = (x) => comPlano((p, rel) => { p.shots[shotEm(p, rel)][1] = x; });
  const seguirPessoa = comPlano((p) => { p.tipo = "pessoa"; p.shots = [[0, larguraVideo(p) / 2]]; });
  const telaCheia = comPlano((p) => {
    if (p.tipo === "outro" && p.tipo_original) { p.tipo = p.tipo_original; delete p.tipo_original; }
    else if (p.tipo !== "outro") { p.tipo_original = p.tipo; p.tipo = "outro"; }
  });
  // trocar o layout de um trecho por outro (a detecção errou): um layout que já aparece neste corte ou um modelo
  const CAMPOS_LAYOUT = ["tipo", "w", "h", "tiles", "centros", "lado", "pequenos", "grande", "rosto", "rosto_guardado", "rosto_manual"];
  const chaveLayout = (p) => (p.tipo === "quadros" ? `q${p.tiles.length}`
    : p.tipo === "lateral" ? `l${p.lado}${p.pequenos.length}-${Math.floor(p.grande[0] / 16)}` : p.tipo === "pessoa" ? "pessoa" : "outro");  // = layout_key
  const descreverLayout = (p) => (p.tipo === "quadros" ? `${p.tiles.length} quadros lado a lado`
    : p.tipo === "lateral" ? `câmeras laterais: ${p.pequenos.length} à ${p.lado === "esq" ? "esquerda" : "direita"}`
    : p.tipo === "pessoa" ? "recorte vertical seguindo quem aparece" : "tela cheia (quadro inteiro)");
  const MODELOS = [  // geometria do canal (1920x1080) para quando o layout certo não aparece em nenhum trecho do corte
    { tipo: "quadros", h: 1080, tiles: [[0, 960], [960, 960]], centros: [null, null] },
    { tipo: "quadros", h: 1080, tiles: [[0, 636], [642, 636], [1286, 634]], centros: [null, null, null] },
  ];
  const opcoesLayout = (atual) => {
    const vistos = new Set([chaveLayout(atual)]), out = [];
    for (const p of plans) {
      if (p.tipo === "outro" || vistos.has(chaveLayout(p))) continue;
      vistos.add(chaveLayout(p));
      out.push({ rotulo: `${descreverLayout(p)} (como em ${fmt(p.t0).replace(/\.\d$/, "")})`, plano: p });
    }
    for (const m of MODELOS) if (!vistos.has(chaveLayout(m))) out.push({ rotulo: `${descreverLayout(m)} (modelo)`, plano: m });
    return out;
  };
  let escolhendo = null;  // trecho que vai receber o layout enquanto se escolhe a origem na faixa
  const rotuloCurto = (p) => (p.tipo === "quadros" ? `Quadros · ${p.tiles.length}`
    : p.tipo === "lateral" ? `Laterais · ${p.pequenos.length} à ${p.lado === "esq" ? "esq." : "dir."}` : p.tipo === "pessoa" ? "Pessoa" : "Tela cheia");
  const copiarPara = (alvo, fonte) => {
    const i = plans.indexOf(alvo);
    video.currentTime = alvo.t0 + 0.01;   // comPlano age no trecho do instante atual
    aplicarLayout(fonte)();
    if (plans.indexOf(alvo) !== i) desenhar();
  };
  const aplicarLayout = (fonte) => comPlano((p) => {
    p.antes_da_troca ||= Object.fromEntries(CAMPOS_LAYOUT.concat("shots", "tipo_original").filter((k) => k in p).map((k) => [k, structuredClone(p[k])]));
    for (const k of CAMPOS_LAYOUT.concat("tipo_original")) {
      if (fonte[k] !== undefined && k !== "tipo_original") p[k] = structuredClone(fonte[k]);
      else if (k !== "w" && (k !== "h" || p.tipo === "quadros")) delete p[k];  // w/h de "pessoa"/"outro" = tamanho do vídeo
    }
    if (p.tipo === "pessoa") p.shots = [[0, fonte.shots[fonte.t0 < p.t0 ? fonte.shots.length - 1 : 0][1]]];
    else if (p.tipo === "quadros") {  // sem análise de boca nesse trecho: zoom fixo em quem falava no trecho de origem
      const pessoa = fonte.shots ? (fonte.t0 < p.t0 ? fonte.shots[fonte.shots.length - 1][1] : fonte.shots[0][1]) : 0;
      p.shots = [[0, pessoa]];
    } else delete p.shots;
  });
  const voltarLayout = comPlano((p) => {
    for (const k of CAMPOS_LAYOUT.concat("shots", "tipo_original")) delete p[k];
    Object.assign(p, p.antes_da_troca);
    delete p.antes_da_troca;
  });

  const restaurar = () => {
    if (!confirm("Desfazer todas as correções deste assunto e voltar ao que foi detectado?")) return;
    a.layout.plans = structuredClone(a.layout.detectado);
    rotear();
    api("PUT", `/api/projetos/${enc(projeto.nome)}/assuntos/${a.id}/layout`, { plans: a.layout.plans });
  };

  // ---------------------------------------------------------- desenho
  const desenharFaixa = () => {
    const D = duracao(), pct = (t) => `${(100 * t) / D}%`;
    faixa.replaceChildren(...plans.map((p, i) => {
      const fim = i + 1 < plans.length ? plans[i + 1].t0 : D;
      const bloco = h("div", { class: `trecho tipo-${p.tipo}` + (escolhendo ? " escolhivel" : ""), style: `left:${pct(p.t0)};width:${pct(fim - p.t0)}`,
        title: descreverLayout(p), "data-i": i }, h("span", {}, rotuloCurto(p)));
      if (p.tipo === "quadros" || p.tipo === "pessoa") p.shots.forEach(([s, q], k) => {
        const e = k + 1 < p.shots.length ? p.shots[k + 1][0] : fim - p.t0;
        const cor = CORES_PESSOA[(p.tipo === "quadros" ? q : k) % 4];
        bloco.append(h("i", { class: "shot", style: `left:${(100 * s) / (fim - p.t0)}%;width:${(100 * (e - s)) / (fim - p.t0)}%;background:${cor}`, title: p.tipo === "quadros" ? `Pessoa ${q + 1}` : `Enquadramento ${k + 1}` }));
      });
      return bloco;
    }), h("div", { class: "faixa-cabeca", style: `left:${pct(video.currentTime)}` }));
  };
  faixa.onclick = (e) => {
    if (escolhendo) {  // "Copiar de um trecho…": o clique escolhe o trecho de onde vem o layout
      const fonte = plans[+(e.target.closest(".trecho")?.dataset.i ?? -1)];
      const alvo = escolhendo;
      escolhendo = null;
      if (fonte && fonte !== alvo) copiarPara(alvo, fonte);
      desenhar();
      return;
    }
    const r = faixa.getBoundingClientRect();
    video.currentTime = ((e.clientX - r.left) / r.width) * duracao();
  };

  const desenharPainel = () => {
    const i = indiceEm(video.currentTime), p = plans[i], rel = video.currentTime - p.t0;
    const btn = (rotulo, fn, ativo, extra = {}) => h("button", { class: ativo ? "primario" : "", onclick: fn, ...extra }, rotulo);
    const corpo = [];
    if (p.tipo === "quadros") {
      const k = shotEm(p, rel), q = p.shots[k][1];
      corpo.push(h("div", { class: "linha" }, h("b", {}, "Zoom em:"),
        p.tiles.map((_, n) => btn(`Pessoa ${n + 1}`, zoomEm(n), n === q, { style: `border-color:${CORES_PESSOA[n % 4]}` }))));
      corpo.push(h("div", { class: "linha" },
        h("button", { onclick: trocarAqui, title: "Começa uma nova troca de zoom no ponto atual do vídeo" }, "✂ Trocar o zoom a partir daqui"),
        k > 0 ? h("button", { class: "discreto", onclick: removerTroca }, "Remover esta troca") : null,
        h("span", { class: "fraco pequeno" }, `troca ${k + 1} de ${p.shots.length}, começa em ${fmt(p.t0 + p.shots[k][0])}`)));
    }
    if (p.tipo === "pessoa") {
      const k = shotEm(p, rel), W = larguraVideo(p);
      corpo.push(h("div", { class: "linha" }, h("b", {}, "Posição do recorte:"),
        h("span", { class: "fraco pequeno" }, "esquerda"),
        h("input", { type: "range", min: 0, max: W, step: Math.round(W / 200), value: p.shots[k][1], style: "flex:1",
          onchange: (e) => moverRecorte(+e.target.value)() }),  // ao soltar: redesenhar o painel no meio do arrasto soltaria o controle
        h("span", { class: "fraco pequeno" }, "direita")));
      corpo.push(h("div", { class: "linha" },
        h("button", { onclick: trocarAqui, title: "A partir do ponto atual do vídeo o recorte pode ir para outro lugar" }, "✂ Mudar o recorte a partir daqui"),
        k > 0 ? h("button", { class: "discreto", onclick: removerTroca }, "Remover esta troca") : null,
        h("span", { class: "fraco pequeno" }, `enquadramento ${k + 1} de ${p.shots.length}, começa em ${fmt(p.t0 + p.shots[k][0])}`)));
    }
    if (p.tipo === "outro" && !p.tipo_original) corpo.push(h("div", { class: "linha" },
      h("button", { onclick: seguirPessoa, title: "Troca o quadro inteiro por um recorte vertical que você posiciona" }, "Usar recorte vertical neste trecho"),
      h("span", { class: "fraco pequeno" }, "nenhum rosto foi achado aqui: está o quadro inteiro sobre fundo desfocado")));
    if (p.tipo === "lateral") corpo.push(h("div", { class: "linha" }, h("b", {}, "Área grande:"),
      btn("Câmera (zoom no rosto)", areaGrande(true), !!p.rosto), btn("Apresentação (inteira)", areaGrande(false), !p.rosto)));
    const opcoes = opcoesLayout(p);
    const anterior = plans[i - 1], proximo = plans[i + 1];
    const escolha = h("select", {}, opcoes.map((o, n) => h("option", { value: n }, o.rotulo)));
    corpo.push(h("div", { class: "linha" }, h("b", {}, "Usar outro layout neste trecho:"),
      anterior ? h("button", { onclick: () => copiarPara(p, anterior), title: descreverLayout(anterior) }, "← Igual ao anterior") : null,
      proximo ? h("button", { onclick: () => copiarPara(p, proximo), title: descreverLayout(proximo) }, "Igual ao próximo →") : null,
      plans.length > 1 ? h("button", { class: escolhendo ? "primario" : "", onclick: () => { escolhendo = escolhendo ? null : p; desenhar(); } },
        escolhendo ? "Clique na faixa no trecho de origem… (cancelar)" : "Copiar de um trecho…") : null,
      p.antes_da_troca ? h("button", { class: "discreto", onclick: voltarLayout }, "Voltar ao que foi detectado") : null));
    if (opcoes.length) corpo.push(h("div", { class: "linha pequeno" }, h("span", { class: "fraco" }, "ou escolha:"), escolha,
      h("button", { onclick: () => aplicarLayout(opcoes[escolha.value].plano)() }, "Aplicar")));
    painel.replaceChildren(
      h("div", { class: "linha" }, h("b", {}, `Trecho ${i + 1} de ${plans.length}: ${descreverLayout(p)}`),
        h("span", { class: "fraco pequeno" }, `${fmt(p.t0)} → ${fmt(i + 1 < plans.length ? plans[i + 1].t0 : duracao())}`),
        h("span", { style: "flex:1" }),
        p.tipo !== "outro" || p.tipo_original ? btn(p.tipo === "outro" ? "Voltar ao layout detectado" : "Mostrar como tela cheia", telaCheia, false) : null),
      ...corpo);
  };
  const desenhar = () => { desenharFaixa(); desenharPainel(); };

  // ---------------------------------------------------------- vídeo
  let ultimoTrecho = -1, ultimaPrevia = 0;
  video.onloadedmetadata = () => { desenhar(); atualizarPrevia(); };
  video.ontimeupdate = () => {
    desenharFaixa();
    const i = indiceEm(video.currentTime);
    if (i !== ultimoTrecho) { ultimoTrecho = i; desenharPainel(); }
    if (Date.now() - ultimaPrevia > 1000) { ultimaPrevia = Date.now(); atualizarPrevia(); }  // ~1 prévia/s tocando
  };
  video.onseeked = () => { desenharPainel(); atualizarPrevia(); };
  desenhar();

  return h("div", { class: "cartao editor-layout" },
    desatualizado ? h("div", { class: "aviso-linha linha" },
      h("span", { style: "flex:1" }, "⚠ O assunto mudou depois desta análise (início, fim ou resumo). Analise de novo: suas correções continuam nas partes que não mudaram."),
      h("button", { class: "primario", disabled: ocupado(), onclick: reanalisar }, "Analisar de novo")) : null,
    h("div", { class: "layout-topo" }, video, previa),
    faixa,
    h("div", { class: "fraco pequeno linha" },
      h("span", { style: "flex:1" }, "Clique na faixa para ir a um ponto. As correções valem para o trecho do ponto atual e salvam sozinhas."),
      h("button", { class: "discreto", onclick: restaurar }, "Restaurar detecção automática")),
    painel);
}
