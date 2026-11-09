// Etapa 4, cartão "Legenda": estilo do canal com prévia num quadro real e correção do texto.
// Usa h(), api(), fmt(), enc(), projeto de app.js.

let leg = null;            // {estilo, padrao, fontes} do servidor (estilo vale para todas as lives)
const legSel = {};         // por live: {aid, t} (assunto e instante da prévia)
const CAMPOS_LEGENDA = [   // [chave, rótulo, tipo, mín, máx, passo]
  ["fonte", "Fonte", "fonte"],
  ["tamanho", "Tamanho", "num", 8, 120, 1],
  ["tamanho_destaque", "Tamanho da palavra falada", "num", 8, 140, 1],
  ["cor", "Cor", "cor"],
  ["cor_destaque", "Cor da palavra falada", "cor"],
  ["contorno", "Contorno", "num", 0, 10, 0.5],
  ["cor_contorno", "Cor do contorno", "cor"],
  ["sombra", "Sombra", "num", 0, 10, 0.5],
  ["palavras", "Palavras por vez", "num", 1, 8, 1],
  ["pausa", "Manter na tela em pausas de até (s)", "num", 0, 5, 0.25],
  ["margem", "Margem nas laterais (px)", "num", 0, 400, 5],
  ["modo", "Modo", "modo"],
  ["zoom", "Aumentar a palavra falada", "chk"],
  ["negrito", "Negrito", "chk"],
  ["maiusculas", "Tudo maiúsculo", "chk"],
  ["sem_pontuacao", "Sem pontuação", "chk"],
];
const MODOS_LEGENDA = { highlight: "Destacar a palavra falada", colorir: "Colorir o texto conforme a fala",
  no_highlight: "Sem destaque", palavra_por_palavra: "Uma palavra por vez" };

function cartaoLegenda(incluidos) {
  const card = h("div", { class: "cartao" }, h("h2", {}, "Legenda"));
  const com = incluidos.filter((a) => a.layout);
  if (!com.length) {
    card.append(h("p", { class: "fraco" }, "Analise os layouts (etapa 3) para ajustar a legenda vendo a prévia."));
    return card;
  }
  (async () => { leg ||= await api("GET", "/api/legenda"); montarLegenda(card, com); })();
  return card;
}

function montarLegenda(card, com) {
  const st = (legSel[projeto.nome] ||= { aid: com[0].id, t: 5 });
  if (!com.some((a) => a.id === st.aid)) Object.assign(st, { aid: com[0].id, t: 5 });
  const assunto = () => com.find((a) => a.id === st.aid);
  const duracao = () => assunto().layout.trechos_video.reduce((s, [a, b]) => s + b - a, 0);
  const base = () => `/api/projetos/${enc(projeto.nome)}/assuntos/${st.aid}/legenda`;

  // ---------------------------------------------------------- prévia
  const img = h("img", { class: "previa-vertical", alt: "Prévia com legenda" });
  const aviso = h("div", { class: "fraco pequeno" });
  let previaT = null, previaURL = null, pedido = 0;
  const previa = () => {
    clearTimeout(previaT);
    previaT = setTimeout(async () => {
      const n = ++pedido;
      img.classList.add("carregando");
      const r = await fetch(`${base()}/previa`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: st.t, estilo: leg.estilo }) });
      if (n !== pedido) return;  // chegou resposta de um pedido antigo
      img.classList.remove("carregando");
      if (!r.ok) { aviso.textContent = (await r.json().catch(() => ({}))).detail || "Erro na prévia"; return; }
      aviso.textContent = "";
      if (previaURL) URL.revokeObjectURL(previaURL);
      img.src = previaURL = URL.createObjectURL(await r.blob());
    }, 200);
  };
  const tempo = h("input", { type: "range", min: 0, step: 0.1, class: "tempo-previa" });
  const tempoTxt = h("span", { class: "fraco pequeno" });
  const irPara = (t) => { st.t = t; tempo.value = t; tempoTxt.textContent = fmt(t); marcarAtual(); previa(); };
  tempo.oninput = () => irPara(+tempo.value);

  const escolha = h("select", { onchange: () => { st.aid = escolha.value; tempo.max = duracao(); irPara(Math.min(5, duracao())); carregarPalavras(); } },
    com.map((a) => h("option", { value: a.id, selected: a.id === st.aid }, a.titulo)));

  // ---------------------------------------------------------- estilo
  let salvarT = null;
  const mudou = (k, v) => {
    leg.estilo[k] = v;
    previa();
    clearTimeout(salvarT);
    salvarT = setTimeout(() => api("PUT", "/api/legenda", { estilo: leg.estilo }), 400);
  };
  const campo = ([k, rotulo, tipo, min, max, passo]) => {
    const v = leg.estilo[k];
    let el;
    if (tipo === "chk") el = h("input", { type: "checkbox", checked: v, onchange: (e) => mudou(k, e.target.checked) });
    else if (tipo === "cor") el = h("input", { type: "color", value: v, oninput: (e) => mudou(k, e.target.value.toUpperCase()) });
    else if (tipo === "num") el = h("input", { type: "number", min, max, step: passo, value: v, oninput: (e) => e.target.value !== "" && mudou(k, +e.target.value) });
    else if (tipo === "modo") el = h("select", { onchange: (e) => mudou(k, e.target.value) },
      Object.entries(MODOS_LEGENDA).map(([m, nome]) => h("option", { value: m, selected: m === v }, nome)));
    else el = h("select", { onchange: (e) => mudou(k, e.target.value) },
      (leg.fontes.includes(v) ? leg.fontes : [v, ...leg.fontes]).map((f) => h("option", { value: f, selected: f === v }, f)));
    return h("label", { class: tipo === "chk" ? "campo chk" : "campo" }, h("span", {}, rotulo), el);
  };
  const campos = h("div", { class: "campos-legenda" });
  const desenharCampos = () => campos.replaceChildren(...CAMPOS_LEGENDA.map(campo));
  desenharCampos();
  // ---------------------------------------------------------- mais fontes (Google Fonts ou arquivo)
  const sugestoes = h("datalist", { id: "fontes-google" });
  const buscaFonte = h("input", { type: "text", list: "fontes-google", placeholder: "Buscar no Google Fonts (ex.: Montserrat)", size: 30 });
  const avisoFonte = h("span", { class: "fraco pequeno" });
  let buscaT = null;
  buscaFonte.oninput = () => {
    clearTimeout(buscaT);
    buscaT = setTimeout(async () => {
      const r = await fetch(`/api/legenda/google?q=${enc(buscaFonte.value)}`);
      if (r.ok) sugestoes.replaceChildren(...(await r.json()).map((f) => h("option", { value: f.familia }, `${f.pesos.length} pesos`)));
    }, 250);
  };
  const usarFontes = (r) => {  // fonte nova: entra na lista e já fica escolhida
    leg.fontes = r.fontes;
    avisoFonte.textContent = `Adicionada: ${r.adicionadas.join(", ")}`;
    leg.estilo.fonte = r.adicionadas.find((n) => !/ (Thin|Light|ExtraLight|Medium|SemiBold)$/.test(n)) || r.adicionadas[0];
    desenharCampos();
    mudou("fonte", leg.estilo.fonte);
  };
  const baixarFonte = h("button", { onclick: async () => {
    if (!buscaFonte.value.trim()) return;
    baixarFonte.disabled = true; avisoFonte.textContent = "Baixando...";
    try { usarFontes(await api("POST", "/api/legenda/google", { familia: buscaFonte.value.trim() })); buscaFonte.value = ""; }
    catch { avisoFonte.textContent = ""; }
    finally { baixarFonte.disabled = false; }
  } }, "Baixar");
  const arquivoFonte = h("input", { type: "file", accept: ".ttf,.otf", hidden: true, onchange: async () => {
    const fd = new FormData();
    fd.append("arquivo", arquivoFonte.files[0]);
    arquivoFonte.value = "";
    usarFontes(await api("POST", "/api/legenda/fonte", fd));
  } });
  buscaFonte.onkeydown = (e) => e.key === "Enter" && baixarFonte.click();
  const maisFontes = h("div", { class: "linha pequeno" }, h("b", {}, "Mais fontes:"), buscaFonte, sugestoes, baixarFonte,
    h("span", { class: "fraco" }, "ou"), h("button", { onclick: () => arquivoFonte.click() }, "Enviar .ttf/.otf"), arquivoFonte, avisoFonte);

  const padrao = h("button", { class: "discreto", onclick: () => {
    leg.estilo = { ...leg.padrao };
    desenharCampos();
    mudou("fonte", leg.estilo.fonte);
  } }, "Voltar ao padrão");

  // ---------------------------------------------------------- texto (correção palavra por palavra)
  const texto = h("div", { class: "texto-legenda" }, h("span", { class: "fraco" }, "Carregando texto..."));
  let palavras = [];
  const marcarAtual = () => {
    let atual = null;
    for (const el of texto.children) if (+el.dataset.t <= st.t + 0.01) atual = el;
    texto.querySelector(".atual")?.classList.remove("atual");
    atual?.classList.add("atual");
  };
  const editar = (el, [ref, , txt, original]) => {
    const campo = h("input", { type: "text", value: txt === "" ? original : txt, class: "editar-palavra", size: Math.max(4, (txt || original || "").length + 2) });
    let feito = false;
    const fim = async (salvar) => {
      if (feito) return;
      feito = true;
      if (salvar && campo.value.trim() !== txt) {
        await api("PUT", `/api/projetos/${enc(projeto.nome)}/correcoes/palavra`, { ref, texto: campo.value });
        await carregarPalavras();
        previa();
      } else el.replaceWith(palavra(palavras.find((p) => p[0] === ref)));
    };
    campo.onkeydown = (e) => { if (e.key === "Enter") fim(true); if (e.key === "Escape") fim(false); e.stopPropagation(); };
    campo.onblur = () => fim(true);
    el.replaceWith(campo);
    campo.focus();
    campo.select();
  };
  const palavra = (p) => {
    const [, t, txt, original] = p;
    const el = h("span", {
      class: "palavra" + (original != null ? " corrigida" : "") + (txt === "" ? " apagada" : ""),
      "data-t": t,
      title: original != null ? `Era: ${original}${txt === "" ? " (apagada)" : ""}\nDuplo clique para editar` : "Duplo clique para editar",
      onclick: () => irPara(t + 0.05),
      ondblclick: () => editar(el, p),
    }, txt === "" ? original : txt);
    return el;
  };
  async function carregarPalavras() {
    palavras = await api("GET", base());
    texto.replaceChildren(...palavras.map(palavra));
    marcarAtual();
  }

  // ---------------------------------------------------------- trocas em toda a live
  let trocas = (projeto.correcoes?.trocas || []).map((t) => ({ ...t }));
  const listaTrocas = h("div", { class: "linha" });
  const desenharTrocas = () => listaTrocas.replaceChildren(...trocas.map((t, i) =>
    h("span", { class: "selo troca" }, `${t.de} → ${t.para || "(apagar)"}`, t.n != null ? h("span", { class: "fraco" }, ` ${t.n}×`) : null,
      h("button", { class: "discreto", title: "Remover esta troca", onclick: () => salvarTrocas(trocas.filter((_, j) => j !== i)) }, "✕"))));
  const salvarTrocas = async (novas) => {
    trocas = await api("PUT", `/api/projetos/${enc(projeto.nome)}/correcoes/trocas`, { trocas: novas });
    projeto.correcoes = { ...(projeto.correcoes || {}), trocas: trocas.map(({ de, para }) => ({ de, para })) };
    desenharTrocas();
    await carregarPalavras();
    previa();
  };
  const de = h("input", { type: "text", placeholder: "palavra errada", size: 14 });
  const para = h("input", { type: "text", placeholder: "como deve ficar", size: 14 });
  const adicionar = () => {
    if (!de.value.trim()) return;
    salvarTrocas([...trocas.filter((t) => t.de.toLowerCase() !== de.value.trim().toLowerCase()), { de: de.value, para: para.value }]);
    de.value = para.value = "";
  };
  para.onkeydown = (e) => e.key === "Enter" && adicionar();
  desenharTrocas();

  card.append(
    h("div", { class: "legenda-grade" },
      h("div", {}, img, aviso),
      h("div", {},
        h("div", { class: "linha" }, h("b", {}, "Prévia em:"), escolha),
        h("div", { class: "linha" }, tempo, tempoTxt),
        campos,
        maisFontes,
        h("div", { class: "linha" }, h("span", { class: "fraco pequeno", style: "flex:1" },
          "O estilo vale para todos os cortes do canal. Fontes com vários pesos aparecem um por um na lista (ex.: \"Montserrat Black\")."), padrao))),
    h("h3", {}, "Texto"),
    h("p", { class: "fraco pequeno" }, "Clique numa palavra para ver a prévia nela; duplo clique para corrigir (Enter salva, Esc cancela, vazio tira a palavra). Corrigidas ficam marcadas."),
    texto,
    h("div", { class: "linha" }, h("b", {}, "Trocar em toda a live:"), de, para, h("button", { onclick: adicionar }, "Trocar")),
    listaTrocas);
  tempo.max = duracao();
  irPara(Math.min(st.t, duracao()));
  carregarPalavras();
}
