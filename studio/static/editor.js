// Etapa 2: editor de assuntos — player + transcrição + timeline da live inteira + painel do assunto.
// Usa h(), api(), fmt(), lerTempo(), dur(), enc(), projeto, salvarAssuntos(), ocupado() de app.js.

const transcricoes = {};                      // cache por projeto: [[início, fim, texto], ...]
const estadoEditor = {};                      // seleção e zoom por projeto (sobrevivem a re-render)
let limparEditor = () => {};

const PASSOS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const RESPIRO = 0.12;                         // margem em volta da fala (igual a cortes.RESPIRO)

function etapaAssuntos() {
  limparEditor();
  const raiz = h("div", { class: "editor" }, h("p", { class: "fraco" }, "Carregando transcrição..."));
  const nome = projeto.nome;
  (transcricoes[nome] ? Promise.resolve(transcricoes[nome]) : api("GET", `/api/projetos/${enc(nome)}/transcricao`))
    .then((fr) => { transcricoes[nome] = fr; if (projeto?.nome === nome) montarEditor(raiz, fr); });
  return raiz;
}

function montarEditor(raiz, frases) {
  const st = (estadoEditor[projeto.nome] ||= { sel: projeto.assuntos[0]?.id || null, px: null, desfazer: [], refazer: [], abertos: {} });
  st.abertos ||= {};
  const assuntos = projeto.assuntos;
  let estado = JSON.stringify(assuntos);  // última versão conhecida, para o Ctrl+Z
  const duracaoLive = frases.length ? frases[frases.length - 1][1] + 5 : 60;
  const porId = (id) => assuntos.find((a) => a.id === id);
  const selecionado = () => porId(st.sel);

  // ---------------------------------------------------------- frases: busca e encaixe
  const inicios = frases.map((f) => f[0]);
  const fraseEm = (t) => {               // índice da última frase que começa antes de t
    let lo = 0, hi = frases.length - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (inicios[m] <= t) lo = m; else hi = m - 1; }
    return lo;
  };
  const maisProxima = (t, col) => {
    const i = fraseEm(t);
    return [i - 1, i, i + 1].filter((k) => k >= 0 && k < frases.length)
      .reduce((best, k) => (Math.abs(frases[k][col] - t) < Math.abs(frases[best][col] - t) ? k : best), i);
  };
  const encaixeInicio = (t) => frases.length ? frases[maisProxima(t, 0)][0] : t;
  const encaixeFim = (t) => frases.length ? frases[maisProxima(t, 1)][1] : t;
  const frasesDo = (a) => {
    const out = [];
    for (let k = fraseEm(a.inicio); k < frases.length && frases[k][0] <= a.fim; k++) {
      const meio = (frases[k][0] + frases[k][1]) / 2;
      if (meio >= a.inicio && meio <= a.fim) out.push(k);
    }
    return out;
  };
  const trechos = (a) => {
    if (a.resumo) {
      const out = [];
      for (const k of a.resumo.frases) {
        const [s, e] = frases[k];
        if (out.length && s - out[out.length - 1][1] < 0.3) out[out.length - 1][1] = e; else out.push([s, e]);
      }
      return out;
    }
    // frases desligadas: sai a frase e a pausa até a vizinha (mesma regra de cortes.trechos_do_assunto)
    let out = [[a.inicio, a.fim]];
    for (const k of [...new Set(a.desligadas || [])].sort((x, y) => x - y)) {
      if (!frases[k]) continue;
      const s = Math.min(k > 0 ? frases[k - 1][1] + RESPIRO : frases[k][0], frases[k][0]);
      const e = Math.max(k + 1 < frases.length ? frases[k + 1][0] - RESPIRO : frases[k][1], frases[k][1]);
      out = out.flatMap(([x, y]) => [[x, Math.min(y, s)], [Math.max(x, e), y]]).filter(([x, y]) => y - x >= 0.3);
    }
    return out;
  };
  const picotado = (a) => a.resumo || a.desligadas?.length;
  const filhosDe = (id) => assuntos.filter((a) => a.pai === id);
  const paiDe = (a) => a.pai && porId(a.pai);
  const novoId = () => Math.random().toString(16).slice(2, 10);
  const duracao = (a) => trechos(a).reduce((s, [i, f]) => s + f - i, 0);

  // ---------------------------------------------------------- player
  const video = h("video", { controls: true, preload: "auto",
    src: `/media/${enc(projeto.nome)}/${projeto.arquivos.proxy ? "proxy.mp4" : "input.mp4"}` });
  let fila = null;                        // trechos tocando (assunto / resumo)
  const tocarTrechos = (lista) => { fila = lista.map((t) => [...t]); video.currentTime = fila[0][0]; video.play(); };
  const relogio = h("span", { class: "relogio" }, "0:00.0");

  // ---------------------------------------------------------- transcrição
  const transcricao = h("div", { class: "transcricao" }, frases.map(([s, , texto], i) =>
    h("p", { class: "frase", "data-i": i },
      h("span", { class: "marca-resumo", title: "Ligar/desligar esta frase no corte" }),
      h("span", { class: "t" }, fmt(s).replace(/\.\d$/, "")),
      h("span", { class: "txt" }, texto),
      h("span", { class: "acoes-frase" },
        h("button", { class: "mini", "data-acao": "ini", title: "Início do assunto aqui" }, "⇤"),
        h("button", { class: "mini", "data-acao": "fim", title: "Fim do assunto aqui" }, "⇥")))));
  const pFrase = (i) => transcricao.children[i];
  transcricao.onclick = (e) => {
    const p = e.target.closest(".frase"); if (!p) return;
    const i = +p.dataset.i, a = selecionado(), acao = e.target.dataset.acao;
    if (acao && a) {
      if (acao === "ini" && frases[i][0] < a.fim - 1) a.inicio = frases[i][0];
      if (acao === "fim" && frases[i][1] > a.inicio + 1) a.fim = frases[i][1];
      podarResumo(a); alterado();
    } else if (e.target.classList.contains("marca-resumo") && a && frasesDo(a).includes(i)) {
      if (a.resumo) {                     // resumo: a lista é das frases mantidas
        const set = new Set(a.resumo.frases);
        set.has(i) ? set.delete(i) : set.add(i);
        a.resumo.frases = [...set].sort((x, y) => x - y);
        if (!a.resumo.frases.length) delete a.resumo;
      } else {                            // corte normal: a lista é das frases desligadas
        const set = new Set(a.desligadas || []);
        set.has(i) ? set.delete(i) : set.add(i);
        a.desligadas = [...set].sort((x, y) => x - y);
        if (!a.desligadas.length) delete a.desligadas;
      }
      alterado();
    } else {
      video.currentTime = frases[i][0];
    }
  };
  let fraseAtual = -1;
  const marcarTranscricao = () => {
    transcricao.querySelectorAll(".dentro, .mantida, .cortada").forEach((p) => p.classList.remove("dentro", "mantida", "cortada"));
    const a = selecionado(); if (!a) return;
    const manter = a.resumo ? new Set(a.resumo.frases) : null, fora = new Set(a.desligadas || []);
    for (const k of frasesDo(a)) {
      const p = pFrase(k);
      p.classList.add("dentro", (manter ? manter.has(k) : !fora.has(k)) ? "mantida" : "cortada");
    }
  };

  // ---------------------------------------------------------- timeline
  const conteudo = h("div", { class: "tl-conteudo" });
  const tl = h("div", { class: "tl" }, conteudo);
  const cabeca = h("div", { class: "tl-cabeca" });
  const ajustar = () => (st.px = Math.max(0.02, (tl.clientWidth - 20) / duracaoLive));
  const zoom = (fator, ancoraT) => {
    const t = ancoraT ?? (tl.scrollLeft + tl.clientWidth / 2) / st.px;
    const xTela = t * st.px - tl.scrollLeft;
    st.px = Math.min(80, Math.max((tl.clientWidth - 20) / duracaoLive, st.px * fator));
    desenharTimeline();
    tl.scrollLeft = t * st.px - xTela;
  };
  tl.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const r = tl.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.25 : 0.8, (tl.scrollLeft + e.clientX - r.left) / st.px);
  }, { passive: false });

  const desenharTimeline = () => {
    if (!st.px) ajustar();
    const px = st.px, largura = duracaoLive * px;
    conteudo.style.width = `${largura}px`;
    const passo = PASSOS.find((p) => p * px >= 90) || 3600;
    const regua = h("div", { class: "tl-regua" });
    for (let t = 0; t < duracaoLive; t += passo) regua.append(h("span", { style: `left:${t * px}px` }, fmt(t).replace(/\.\d$/, "")));
    const blocos = assuntos.map((a) => {
      const b = h("div", { class: "bloco" + (a.id === st.sel ? " sel" : "") + (a.incluir ? "" : " excluido") + (picotado(a) ? " resumido" : "") + (a.pai ? " filho" : ""),
        style: `left:${a.inicio * px}px;width:${Math.max(4, (a.fim - a.inicio) * px)}px`, title: a.titulo },
        h("span", { class: "rotulo" }, `${numeroAssunto(a, assuntos)}. ${a.titulo}`),
        picotado(a) ? trechos(a).map(([s, e]) => h("i", { class: "pedaco", style: `left:${(s - a.inicio) * px}px;width:${Math.max(1, (e - s) * px)}px` })) : null,
        h("b", { class: "alca esq", "data-lado": "inicio" }), h("b", { class: "alca dir", "data-lado": "fim" }));
      b.dataset.id = a.id;
      return b;
    });
    conteudo.replaceChildren(regua, ...blocos, cabeca);
    moverCabeca();
  };
  const moverCabeca = () => (cabeca.style.left = `${video.currentTime * st.px}px`);

  // Bordas = redimensionar; meio do bloco = mover o assunto inteiro; clique sem arrastar = selecionar.
  // Os listeners ficam na JANELA: redesenhar a timeline troca os elementos no meio do arraste.
  conteudo.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const bloco = e.target.closest(".bloco");
    if (!bloco) { video.currentTime = Math.max(0, (e.clientX - conteudo.getBoundingClientRect().left) / st.px); return; }
    e.preventDefault();
    const a = porId(bloco.dataset.id);
    const lado = e.target.dataset.lado || "mover";
    const x0 = e.clientX, ini0 = a.inicio, fim0 = a.fim, tamanho = fim0 - ini0;
    const filhos0 = lado === "mover" ? filhosDe(a.id).map((f) => [f, f.inicio, f.fim]) : [];  // vão junto com o assunto
    const pai = paiDe(a);                 // corte de dentro não sai do assunto
    let arrastou = false;
    if (st.sel !== a.id) selecionar(a.id, false);
    const mover = (ev) => {
      if (!arrastou && Math.abs(ev.clientX - x0) < 4) return;  // tremida do clique não conta como arraste
      arrastou = true;
      const dt = (ev.clientX - x0) / st.px, livre = ev.altKey;
      if (lado === "inicio") {
        const t = livre ? ini0 + dt : encaixeInicio(ini0 + dt);
        a.inicio = Math.min(Math.max(0, t), a.fim - 1);
        video.currentTime = a.inicio;
      } else if (lado === "fim") {
        const t = livre ? fim0 + dt : encaixeFim(fim0 + dt);
        a.fim = Math.max(t, a.inicio + 1);
        video.currentTime = a.fim;
      } else {
        const s = Math.min(Math.max(0, ini0 + dt), duracaoLive - tamanho);
        a.inicio = livre ? s : encaixeInicio(s);
        a.fim = livre ? a.inicio + tamanho : Math.max(encaixeFim(a.inicio + tamanho), a.inicio + 1);
        video.currentTime = a.inicio;
        for (const [f, i0, f0] of filhos0) { f.inicio = i0 + a.inicio - ini0; f.fim = f0 + a.inicio - ini0; }
      }
      if (pai) { a.inicio = Math.max(a.inicio, pai.inicio); a.fim = Math.min(a.fim, pai.fim); }
      desenharTimeline();
    };
    const soltar = () => {
      window.removeEventListener("pointermove", mover);
      window.removeEventListener("pointerup", soltar);
      if (arrastou) { podarResumo(a); filhos0.forEach(([f]) => podarResumo(f)); alterado(); }
    };
    window.addEventListener("pointermove", mover);
    window.addEventListener("pointerup", soltar);
  });

  // ---------------------------------------------------------- painel do assunto
  const painel = h("div", { class: "painel-assunto cartao" });
  const nivel = h("select", { title: "Quanto cortar. O Claude decide a duração: o que importa é o corte continuar fazendo sentido." },
    [["leve", "leve (só repetições e vícios)"], ["medio", "médio"], ["forte", "forte (só o essencial)"]]
      .map(([v, t]) => h("option", { value: v, selected: v === "medio" }, t)));
  const modeloResumo = h("select", {}, ["sonnet", "opus", "haiku"].map((m) => h("option", {}, m)));
  const desenharPainel = () => {
    const a = selecionado();
    if (!a) return painel.replaceChildren(h("p", { class: "fraco" }, "Selecione um assunto na timeline ou na lista."));
    const pai = paiDe(a);
    const tempo = (campo) => h("input", { type: "text", value: fmt(a[campo]), onchange: (e) => {
      const v = lerTempo(e.target.value);
      if (!isNaN(v)) { a[campo] = v; podarResumo(a); }
      alterado();
    } });
    const ocupar = async (botao, texto, fn) => {
      const antes = botao.textContent;
      botao.disabled = true; botao.textContent = texto;
      try { await salvarAgora(); await fn(); } finally { botao.disabled = false; botao.textContent = antes; }
    };
    const bResumir = h("button", { onclick: () => ocupar(bResumir, "Claude resumindo...", async () => {
      const novo = await api("POST", `/api/projetos/${enc(projeto.nome)}/assuntos/${a.id}/resumir`, { modelo: modeloResumo.value, nivel: nivel.value });
      a.resumo = novo.resumo;
      alterado();
    }) }, a.resumo ? "Resumir de novo" : "Resumir");
    const bDentro = h("button", { onclick: () => ocupar(bDentro, "Claude procurando...", async () => {
      const achados = await api("POST", `/api/projetos/${enc(projeto.nome)}/assuntos/${a.id}/subcortes`, { modelo: modeloResumo.value });
      const existentes = filhosDe(a.id);
      const novos = achados.filter((c) => !existentes.some((f) => c.inicio < f.fim && c.fim > f.inicio));
      if (!novos.length) return alert(achados.length ? "O Claude só achou cortes que já existem aqui dentro." : "O Claude não achou cortes menores que funcionem sozinhos neste assunto.");
      for (const c of novos) assuntos.push({ id: novoId(), pai: a.id, titulo: c.titulo, inicio: c.inicio, fim: c.fim, incluir: true, nota: 0, motivo: c.motivo });
      st.abertos[a.id] = true;
      alterado();
    }), title: "O Claude procura cortes menores que funcionem sozinhos, só dentro deste assunto" }, "✨ Claude: cortes aqui dentro");
    painel.replaceChildren(
      pai ? h("div", { class: "linha pequeno fraco" }, "Corte dentro de ",
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); selecionar(pai.id, true); } }, `${numeroAssunto(pai, assuntos)}. ${pai.titulo}`)) : null,
      h("div", { class: "linha" },
        h("span", { class: "selo" }, numeroAssunto(a, assuntos)),
        h("input", { type: "text", class: "titulo", value: a.titulo, onchange: (e) => { a.titulo = e.target.value; alterado(); } }),
        h("label", { class: "pequeno" }, h("input", { type: "checkbox", checked: a.incluir, onchange: (e) => { a.incluir = e.target.checked; alterado(); } }), " exportar")),
      h("div", { class: "linha tempos" },
        h("button", { onclick: () => tocarTrechos(trechos(a)), title: "Tocar sem as frases desligadas (atalho: Enter)" }, a.resumo ? "▶ resumo" : "▶ tocar"),
        h("button", { class: "discreto", onclick: () => definir("inicio"), title: "Início no ponto atual (I)" }, "⇤"), tempo("inicio"),
        h("span", { class: "fraco" }, "→"), tempo("fim"),
        h("button", { class: "discreto", onclick: () => definir("fim"), title: "Fim no ponto atual (O)" }, "⇥"),
        h("span", { class: "fraco pequeno" }, picotado(a) ? `${dur(duracao(a))} (de ${dur(a.fim - a.inicio)})` : dur(a.fim - a.inicio)),
        a.nota ? h("span", { class: "selo" }, `nota ${a.nota}`) : null),
      a.motivo ? h("p", { class: "fraco pequeno" }, a.motivo) : null,
      h("div", { class: "linha" },
        h("button", { onclick: dividir, title: "Dividir no ponto atual (S)" }, "✂ Dividir aqui"),
        h("button", { onclick: juntar, disabled: !proximo(a), title: "Juntar com o próximo (J)" }, "⊕ Juntar com o próximo"),
        h("button", { onclick: () => zoomNo(a) }, "🔍 Focar"),
        h("button", { class: "discreto perigo", onclick: apagar }, "Apagar")),
      pai ? null : h("div", { class: "linha" },
        h("b", {}, "Cortes aqui dentro"),
        h("button", { onclick: novoDentro, title: "Novo corte dentro deste assunto, a partir do ponto atual do vídeo" }, "+ Corte aqui dentro"),
        bDentro),
      h("div", { class: "resumo-caixa" },
        h("div", { class: "linha" }, h("b", {}, "Resumo rápido"), nivel, modeloResumo, bResumir,
          a.resumo ? h("button", { class: "discreto", onclick: () => { delete a.resumo; alterado(); } }, "Desfazer resumo") : null),
        h("p", { class: "fraco pequeno" }, a.resumo
          ? `${a.resumo.frases.length} frases mantidas. Clique no círculo à esquerda de uma frase para incluir ou tirar. Pausas longas dentro das frases são cortadas na exportação.`
          : "Clique no círculo à esquerda de uma frase para desligar ou ligar (o resto do corte fica como está). " +
            "Ou o Claude resume: escolhe só frases dentro deste assunto, mantendo o sentido; a duração é ele quem decide.")));
  };

  // ---------------------------------------------------------- lista
  const lista = h("div", { class: "lista-assuntos" });
  const resumoLista = h("p", { class: "fraco pequeno" });
  const desenharLista = () => {
    const inc = assuntos.filter((a) => a.incluir);
    resumoLista.textContent = assuntos.length
      ? `${inc.length} de ${assuntos.length} cortes para exportar · ${dur(inc.reduce((s, a) => s + duracao(a), 0))} no total` : "";
    const item = (a) => {
      const filhos = a.pai ? [] : filhosDe(a.id);
      const aberto = st.abertos[a.id] || filhos.some((f) => f.id === st.sel);
      return [
        h("div", { class: "item-assunto" + (a.id === st.sel ? " sel" : "") + (a.incluir ? "" : " excluido") + (a.pai ? " filho" : ""),
          onclick: () => selecionar(a.id, true) },
          a.pai ? null : h("button", { class: "mini seta", title: aberto ? "Fechar" : "Ver os cortes aqui dentro",
            onclick: (e) => { e.stopPropagation(); st.abertos[a.id] = !aberto; desenharLista(); } }, aberto ? "▾" : "▸"),
          h("span", { class: "fraco" }, `${numeroAssunto(a, assuntos)}.`),
          h("span", { class: "nome" }, a.titulo),
          filhos.length ? h("span", { class: "selo" }, `${filhos.length} dentro`) : null,
          a.resumo ? h("span", { class: "selo" }, "resumido") : a.desligadas?.length ? h("span", { class: "selo" }, `−${a.desligadas.length} frases`) : null,
          h("span", { class: "fraco pequeno" }, `${fmt(a.inicio).replace(/\.\d$/, "")} · ${dur(duracao(a))}`)),
        ...(aberto ? filhos.map((f) => item(f)).flat() : []),
        aberto && !a.pai ? h("div", { class: "item-assunto filho fraco pequeno", onclick: () => { selecionar(a.id, false); novoDentro(); } },
          "+ corte aqui dentro") : null,
      ];
    };
    lista.replaceChildren(...assuntos.filter((a) => !a.pai).flatMap(item).filter(Boolean));
  };

  // ---------------------------------------------------------- ações
  function selecionar(id, mostrar) {
    st.sel = id;
    const a = selecionado();
    desenharTudo();
    if (a && mostrar) {
      video.currentTime = a.inicio;
      tl.scrollLeft = a.inicio * st.px - tl.clientWidth / 4;
      pFrase(frasesDo(a)[0] ?? fraseEm(a.inicio))?.scrollIntoView({ block: "center" });
    }
  }
  function zoomNo(a) {
    st.px = Math.min(80, (tl.clientWidth - 40) / ((a.fim - a.inicio) * 1.3));
    desenharTimeline();
    tl.scrollLeft = (a.inicio - (a.fim - a.inicio) * 0.15) * st.px;
  }
  function podarResumo(a) {                  // frases do resumo e desligadas precisam estar dentro do assunto
    const dentro = new Set(frasesDo(a));
    if (a.resumo) {
      a.resumo.frases = a.resumo.frases.filter((k) => dentro.has(k));
      if (!a.resumo.frases.length) delete a.resumo;
    }
    if (a.desligadas) {
      a.desligadas = a.desligadas.filter((k) => dentro.has(k));
      if (!a.desligadas.length) delete a.desligadas;
    }
  }
  function definir(campo) {
    const a = selecionado(); if (!a) return;
    const t = video.currentTime;
    if (campo === "inicio" && t < a.fim - 1) a.inicio = encaixeInicio(t);
    if (campo === "fim" && t > a.inicio + 1) a.fim = encaixeFim(t);
    podarResumo(a); alterado();
  }
  function proximo(a) {                      // o seguinte do mesmo nível (principal ou dentro do mesmo assunto)
    const grupo = assuntos.filter((x) => (x.pai || null) === (a.pai || null));
    return grupo[grupo.indexOf(a) + 1];
  }
  function dividir() {
    const a = selecionado(); if (!a) return;
    const k = maisProxima(video.currentTime, 0);
    const corte = frases[k][0];
    if (corte <= a.inicio + 1 || corte >= a.fim - 1) return alert("Posicione o vídeo dentro do assunto, longe das bordas.");
    const b = { ...structuredClone(a), id: novoId(), inicio: corte, titulo: `${a.titulo} (parte 2)` };
    a.fim = frases[Math.max(0, k - 1)][1];
    a.titulo = `${a.titulo} (parte 1)`;
    podarResumo(a); podarResumo(b);
    for (const f of filhosDe(a.id)) if ((f.inicio + f.fim) / 2 >= corte) f.pai = b.id;  // cortes de dentro vão com a parte deles
    assuntos.splice(assuntos.indexOf(a) + 1, 0, b);
    alterado();
  }
  function juntar() {
    const a = selecionado(), b = a && proximo(a);
    if (!a || !b) return;
    a.fim = Math.max(a.fim, b.fim);
    const semParte = (t) => t.replace(/ \(parte \d+\)$/, "");
    if (semParte(a.titulo) === semParte(b.titulo)) a.titulo = semParte(a.titulo);  // juntou de volta o que foi dividido
    a.resumo = a.resumo && b.resumo ? { ...a.resumo, frases: [...new Set([...a.resumo.frases, ...b.resumo.frases])].sort((x, y) => x - y) } : undefined;
    if (!a.resumo) delete a.resumo;
    const desl = [...(a.desligadas || []), ...(b.desligadas || [])];
    if (desl.length) a.desligadas = [...new Set(desl)].sort((x, y) => x - y);
    for (const f of filhosDe(b.id)) f.pai = a.id;
    assuntos.splice(assuntos.indexOf(b), 1);
    alterado();
  }
  function apagar() {
    const a = selecionado(); if (!a) return;
    const filhos = filhosDe(a.id);
    if (!confirm(`Apagar "${a.titulo}"${filhos.length ? ` e os ${filhos.length} cortes dentro dele` : ""}?`)) return;
    const n = assuntos.indexOf(a);
    for (const x of [a, ...filhos]) assuntos.splice(assuntos.indexOf(x), 1);
    st.sel = a.pai || assuntos[Math.min(n, assuntos.length - 1)]?.id || null;
    alterado();
  }
  function novoDentro() {
    const a = selecionado(); if (!a || a.pai) return;
    const t = video.currentTime >= a.inicio && video.currentTime < a.fim - 1 ? video.currentTime : a.inicio;
    const ini = Math.max(a.inicio, encaixeInicio(t));
    const fim = Math.min(a.fim, Math.max(encaixeFim(ini + 30), ini + 1));
    const f = { id: novoId(), pai: a.id, titulo: "Corte", inicio: ini, fim, incluir: true, nota: 0, motivo: "" };
    assuntos.push(f);
    st.abertos[a.id] = true;
    st.sel = f.id;
    alterado();
  }
  function novo() {
    const k = fraseEm(video.currentTime);
    const fim = frases[maisProxima(frases[k][0] + 60, 1)][1];
    const a = { id: Math.random().toString(16).slice(2, 10), titulo: "Novo assunto", inicio: frases[k][0], fim, incluir: true, nota: 0, motivo: "" };
    assuntos.push(a);
    st.sel = a.id;
    alterado();
  }

  function alterado() {
    assuntos.sort((x, y) => x.inicio - y.inicio || !!x.pai - !!y.pai);  // empate: o assunto antes dos cortes de dentro
    const atual = JSON.stringify(assuntos);
    if (atual !== estado) {
      st.desfazer.push(estado);
      if (st.desfazer.length > 100) st.desfazer.shift();
      st.refazer = [];
      estado = atual;
    }
    salvarAssuntos();
    desenharTudo();
  }
  function voltar(de, para) {             // Ctrl+Z (de = desfazer) / Ctrl+Y (de = refazer)
    if (!de.length) return;
    para.push(estado);
    estado = de.pop();
    assuntos.splice(0, assuntos.length, ...JSON.parse(estado));
    if (!porId(st.sel)) st.sel = assuntos[0]?.id || null;
    salvarAssuntos();
    desenharTudo();
  }
  const desfazer = () => voltar(st.desfazer, st.refazer);
  const refazer = () => voltar(st.refazer, st.desfazer);
  const salvarAgora = salvarPendente;
  function desenharTudo() { desenharTimeline(); desenharPainel(); desenharLista(); marcarTranscricao(); }

  // ---------------------------------------------------------- reprodução
  video.ontimeupdate = () => {
    const t = video.currentTime;
    relogio.textContent = fmt(t);
    moverCabeca();
    if (fila) {
      if (t >= fila[0][1] - 0.05) {
        fila.shift();
        if (fila.length) video.currentTime = fila[0][0]; else { video.pause(); fila = null; }
      }
    }
    const k = fraseEm(t);
    if (k !== fraseAtual) {
      pFrase(fraseAtual)?.classList.remove("atual");
      pFrase(k)?.classList.add("atual");
      fraseAtual = k;
      if (!video.paused && seguir.checked) pFrase(k)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  };
  video.onseeking = () => { if (fila && !fila.some(([s, e]) => video.currentTime >= s - 0.3 && video.currentTime <= e)) fila = null; };

  // ---------------------------------------------------------- teclado
  const teclas = (e) => {
    if (e.target.matches?.("input, select, textarea")) return;  // dentro de campo de texto: desfazer do próprio campo
    if (e.ctrlKey || e.metaKey) {
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); desfazer(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); refazer(); }
      return;                              // Ctrl+S, Ctrl+J... não disparam os atalhos de uma letra
    }
    if (e.altKey) return;
    const mapa = { " ": () => (video.paused ? video.play() : video.pause()), i: () => definir("inicio"), o: () => definir("fim"),
      s: dividir, j: juntar, Enter: () => selecionado() && tocarTrechos(trechos(selecionado())),
      ArrowLeft: () => (video.currentTime -= 5), ArrowRight: () => (video.currentTime += 5) };
    const f = mapa[e.key] || mapa[e.key.toLowerCase()];
    if (f) { e.preventDefault(); f(); }
  };
  document.addEventListener("keydown", teclas);
  const aoRedimensionar = () => { if (st.px && st.px * duracaoLive < tl.clientWidth - 20) ajustar(); desenharTimeline(); };
  window.addEventListener("resize", aoRedimensionar);
  limparEditor = () => {
    document.removeEventListener("keydown", teclas);
    window.removeEventListener("resize", aoRedimensionar);
    salvarAgora().catch(() => {});
    video.pause();
  };

  // ---------------------------------------------------------- chat com o Claude
  const conversa = (projeto.chat ||= { mensagens: [] });
  const frase = (i) => frases[Math.min(Math.max(0, i | 0), frases.length - 1)];
  const nomeDe = (id) => (porId(id) ? `"${porId(id).titulo}"` : `(assunto ${id} não encontrado)`);

  const descrever = (ops) => {
    const mexidos = new Set(ops.filter((o) => o.op === "alterar" || o.op === "apagar").map((o) => o.id));
    return ops.map((o) => {
      if (o.op === "criar") {
        const s = frase(o.inicio_frase)[0], e = frase(o.fim_frase)[1];
        const sobre = assuntos.filter((a) => !mexidos.has(a.id) && a.inicio < e && a.fim > s);
        return `Criar "${o.titulo || "Novo assunto"}" (${fmt(s)} → ${fmt(e)}, ${dur(e - s)})` +
          (sobre.length ? ` ⚠ sobrepõe ${sobre.map((a) => `"${a.titulo}"`).join(", ")}` : "");
      }
      if (o.op === "alterar") {
        const m = [];
        if (o.titulo) m.push(`título → "${o.titulo}"`);
        if (o.inicio_frase != null) m.push(`início → ${fmt(frase(o.inicio_frase)[0])}`);
        if (o.fim_frase != null) m.push(`fim → ${fmt(frase(o.fim_frase)[1])}`);
        return `Alterar ${nomeDe(o.id)}: ${m.join(", ") || "nada"}`;
      }
      if (o.op === "apagar") return `Apagar ${nomeDe(o.id)}`;
      if (o.op === "resumir") return `Resumir ${nomeDe(o.id)} (${(o.frases || []).length} frases, ~${dur((o.frases || []).reduce((s, k) => s + frase(k)[1] - frase(k)[0], 0))})`;
      if (o.op === "desfazer_resumo") return `Desfazer o resumo de ${nomeDe(o.id)}`;
      if (o.op === "incluir") return `${o.incluir === false ? "Tirar" : "Incluir"} ${nomeDe(o.id)} ${o.incluir === false ? "da" : "na"} exportação`;
      return `Operação desconhecida: ${o.op}`;
    });
  };

  const aplicar = (ops) => {
    for (const o of ops) {
      if (o.op === "criar") {
        const s = frase(o.inicio_frase)[0], e = frase(o.fim_frase)[1];
        if (e - s >= 1) assuntos.push({ id: novoId(), titulo: o.titulo || "Novo assunto", inicio: s, fim: e, incluir: true, nota: 0, motivo: "Criado pelo chat" });
        continue;
      }
      const a = porId(o.id);
      if (!a) continue;
      if (o.op === "alterar") {
        if (o.titulo) a.titulo = o.titulo;
        const s = o.inicio_frase != null ? frase(o.inicio_frase)[0] : a.inicio, e = o.fim_frase != null ? frase(o.fim_frase)[1] : a.fim;
        if (e - s >= 1) { a.inicio = s; a.fim = e; }
        podarResumo(a);
      } else if (o.op === "apagar") {
        for (const x of [a, ...filhosDe(a.id)]) assuntos.splice(assuntos.indexOf(x), 1);
        if (st.sel === a.id) st.sel = assuntos[0]?.id || null;
      } else if (o.op === "resumir") {
        const dentro = new Set(frasesDo(a));
        const fs = [...new Set((o.frases || []).filter((k) => dentro.has(k)))].sort((x, y) => x - y);
        if (fs.length) a.resumo = { frases: fs };
      } else if (o.op === "desfazer_resumo") delete a.resumo;
      else if (o.op === "incluir") a.incluir = o.incluir !== false;
    }
    alterado();
  };

  const comNegrito = (texto) => texto.split(/(\*\*[^*]+\*\*)/).map((p) => (p.startsWith("**") ? h("b", {}, p.slice(2, -2)) : p));
  const mensagensEl = h("div", { class: "chat-msgs" });
  const lerAuto = () => { try { return localStorage.getItem("chat-auto") === "1"; } catch { return false; } };
  const auto = h("input", { type: "checkbox", checked: lerAuto(), onchange: (e) => { try { localStorage.setItem("chat-auto", e.target.checked ? "1" : "0"); } catch {} } });
  const modeloChat = h("select", {}, ["sonnet", "opus", "haiku"].map((m) => h("option", {}, m)));
  const marcarEstado = (i, estado) => { conversa.mensagens[i].estado = estado; api("PUT", `/api/projetos/${enc(projeto.nome)}/chat/${i}`, { estado }); desenharChat(); };

  const desenharChat = () => {
    mensagensEl.replaceChildren(...(conversa.mensagens.length ? conversa.mensagens.map((m, i) => {
      if (m.de === "voce") return h("div", { class: "msg voce" }, m.texto);
      const ops = m.operacoes || [];
      return h("div", { class: "msg claude" },
        h("div", { class: "texto" }, comNegrito(m.texto)),
        ops.length ? h("div", { class: "proposta" + (m.estado ? ` ${m.estado}` : "") },
          h("b", {}, "Proposta"),
          h("ul", {}, descrever(ops).map((d) => h("li", { class: d.includes("⚠") ? "aviso" : "" }, d))),
          m.estado === "pendente"
            ? h("div", { class: "linha" },
                h("button", { class: "primario", onclick: () => { aplicar(ops); marcarEstado(i, "aceita"); } }, "Aceitar"),
                h("button", { onclick: () => marcarEstado(i, "descartada") }, "Descartar"))
            : h("span", { class: "fraco pequeno" }, m.estado === "aceita" ? "✓ aplicada" : "descartada")) : null);
    }) : [h("p", { class: "fraco pequeno" },
      'Peça mudanças nos assuntos ou faça perguntas sobre a live. Ex.: "divida o assunto 3 em dois", "acha mais assuntos sobre o STF", ' +
      '"deixa todos com menos de 3 minutos", "resume o assunto 2 em 40 segundos".')]));
    mensagensEl.scrollTop = mensagensEl.scrollHeight;
  };

  const entrada = h("textarea", { rows: 2, placeholder: "Escreva para o Claude... (Enter envia, Shift+Enter quebra linha)" });
  const enviar = h("button", { class: "primario" }, "Enviar");
  const mandar = async () => {
    const texto = entrada.value.trim();
    if (!texto) return;
    entrada.value = ""; entrada.disabled = enviar.disabled = true;
    conversa.mensagens.push({ de: "voce", texto });
    desenharChat();
    const espera = h("div", { class: "msg claude fraco" }, conversa.sessao ? "Claude pensando..." : "Claude lendo a live inteira (a primeira mensagem pode levar até 1 min)...");
    mensagensEl.append(espera);
    mensagensEl.scrollTop = mensagensEl.scrollHeight;
    try {
      await salvarAgora();                     // o Claude precisa ver a lista como está na tela
      const r = await api("POST", `/api/projetos/${enc(projeto.nome)}/chat`, { mensagem: texto, modelo: modeloChat.value });
      conversa.sessao ||= "ok";
      conversa.mensagens.push({ de: "claude", texto: r.texto, operacoes: r.operacoes, estado: r.estado });
      if (r.estado === "pendente" && auto.checked) { aplicar(r.operacoes); marcarEstado(r.indice, "aceita"); }
    } catch {
      conversa.mensagens.pop();
      entrada.value = texto;
    } finally {
      entrada.disabled = enviar.disabled = false;
      desenharChat();
      entrada.focus();
    }
  };
  enviar.onclick = mandar;
  entrada.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); mandar(); } };
  const chatCartao = h("div", { class: "cartao chat" },
    h("div", { class: "linha" }, h("b", { style: "flex:1" }, "Conversar com o Claude"), modeloChat,
      h("label", { class: "pequeno fraco", title: "Aplica as propostas sem pedir confirmação" }, auto, " aplicar automaticamente"),
      h("button", { class: "discreto", onclick: async () => {
        if (!confirm("Começar uma conversa nova? O histórico desta será apagado.")) return;
        await api("POST", `/api/projetos/${enc(projeto.nome)}/chat/nova`);
        conversa.mensagens = []; delete conversa.sessao; desenharChat();
      } }, "nova conversa")),
    mensagensEl,
    h("div", { class: "chat-entrada" }, entrada, enviar));
  desenharChat();

  // ---------------------------------------------------------- montagem
  const modelo = h("select", {}, ["sonnet", "opus", "haiku"].map((m) => h("option", {}, m)));
  const seguir = h("input", { type: "checkbox", checked: true });
  const gerar = async (modo) => {
    if (modo === "novo" && assuntos.length &&
        !confirm("Gerar do zero troca a lista atual por uma nova. A atual fica guardada em Versões e pode ser restaurada. Continuar?")) return;
    await salvarAgora();                     // o Claude precisa ver (e a versão guardar) a lista como está na tela
    const lim = limite.ligado.checked;
    await api("POST", `/api/projetos/${enc(projeto.nome)}/assuntos/gerar`, { modelo: modelo.value, modo,
      duracao_min: lim ? +limite.min.value || 0 : 0, duracao_max: lim ? +limite.max.value || 0 : 0 });
  };
  // limite de duração dos cortes: liga/desliga; fica guardado no navegador (vale para todas as lives)
  const lerLimite = () => { try { return JSON.parse(localStorage.getItem("limite-duracao")) || {}; } catch { return {}; } };
  const guardarLimite = () => {
    try { localStorage.setItem("limite-duracao", JSON.stringify({ ligado: limite.ligado.checked, min: limite.min.value, max: limite.max.value })); } catch {}
    limite.min.disabled = limite.max.disabled = !limite.ligado.checked;
  };
  const numero = (valor) => h("input", { type: "number", min: 0, step: 5, value: valor, style: "width:5em", onchange: () => guardarLimite() });
  const limite = { ligado: h("input", { type: "checkbox", checked: !!lerLimite().ligado, onchange: () => guardarLimite() }),
    min: numero(lerLimite().min ?? 30), max: numero(lerLimite().max ?? 90) };
  limite.min.disabled = limite.max.disabled = !limite.ligado.checked;
  const lendo = ocupado() && tarefa.tipo === "assuntos";
  const botoesGerar = assuntos.length
    ? [h("button", { class: "primario", disabled: ocupado(), onclick: () => gerar("mais"), title: "Seus assuntos continuam; o Claude procura outros na live" },
         lendo ? "Claude lendo a live..." : "Encontrar mais assuntos"),
       h("button", { class: "discreto", disabled: ocupado(), onclick: () => gerar("novo"), title: "Lista nova no lugar da atual (a atual fica em Versões)" }, "Gerar do zero")]
    : [h("button", { class: "primario", disabled: ocupado(), onclick: () => gerar("novo") }, lendo ? "Claude lendo a live..." : "Encontrar assuntos com o Claude")];

  // versões da lista de assuntos (salvas por você ou automaticamente antes de cada geração)
  const caixaVersoes = h("div", { class: "linha pequeno" });
  const desenharVersoes = () => {
    const versoes = projeto.versoes || [];
    const escolha = h("select", {}, versoes.map((v, i) =>
      h("option", { value: i }, `${v.data} · ${v.nome} · ${v.assuntos.filter((a) => !a.pai).length} assuntos`)));
    caixaVersoes.replaceChildren(
      h("b", {}, "Versões"),
      ...(versoes.length ? [escolha, h("button", { disabled: ocupado(), onclick: async () => {
        if (!confirm("Trocar a lista atual por esta versão? A atual fica guardada em Versões.")) return;
        await salvarAgora();
        await api("POST", `/api/projetos/${enc(projeto.nome)}/versoes/${escolha.value}/restaurar`);
        rotear();
      } }, "Restaurar")] : [h("span", { class: "fraco" }, "nenhuma ainda")]),
      h("button", { onclick: async () => {
        const rotulo = prompt("Nome desta versão (opcional):", "");
        if (rotulo === null) return;
        await salvarAgora();
        await api("POST", `/api/projetos/${enc(projeto.nome)}/versoes`, { rotulo });
        projeto.versoes = (await api("GET", `/api/projetos/${enc(projeto.nome)}`)).versoes;
        desenharVersoes();
      }, title: "Guarda a lista de assuntos como está agora (seleções, resumos, cortes de dentro)" }, "Salvar versão"));
  };
  desenharVersoes();

  raiz.replaceChildren(
    h("div", { class: "ed-topo" },
      h("div", { class: "ed-esq" }, video, h("div", { class: "linha pequeno" }, relogio,
        h("label", { class: "fraco" }, seguir, " transcrição segue o vídeo"),
        h("span", { class: "fraco", title: "Espaço: tocar/pausar · I/O: início/fim · S: dividir · J: juntar · Enter: tocar assunto · ←/→: 5 s · Ctrl+Z / Ctrl+Y: desfazer / refazer" }, "⌨ atalhos"))),
      transcricao),
    h("div", { class: "tl-barra linha pequeno" },
      h("b", {}, "Linha do tempo"),
      h("button", { class: "mini", onclick: () => zoom(0.66) }, "−"), h("button", { class: "mini", onclick: () => zoom(1.5) }, "+"),
      h("button", { class: "mini", onclick: () => { ajustar(); desenharTimeline(); } }, "inteira"),
      h("button", { class: "mini", onclick: desfazer, title: "Desfazer (Ctrl+Z)" }, "↶ Desfazer"),
      h("button", { class: "mini", onclick: refazer, title: "Refazer (Ctrl+Y)" }, "↷ Refazer"),
      h("span", { class: "fraco" }, "Ctrl + roda do mouse dá zoom · arraste as bordas dos blocos (Alt: sem encaixar na frase)"),
      h("span", { style: "flex:1" }),
      h("button", { onclick: novo }, "+ Novo assunto aqui")),
    tl,
    h("div", { class: "ed-baixo" },
      painel,
      chatCartao,
      h("div", { class: "cartao" },
        h("div", { class: "linha" },
          ...botoesGerar,
          h("label", { class: "fraco pequeno" }, "modelo ", modelo),
          h("span", { class: "fraco pequeno", title: "O Claude lê a live inteira e diz quantos cortes bons ela tem" }, "o Claude decide quantos cortes"),
          h("label", { class: "fraco pequeno", title: "Desligado: o corte dura o que o assunto durar. Ligado: o Claude só propõe cortes nessa faixa (0 = sem esse lado); vale para a próxima busca, não mexe nos assuntos que já estão na lista" },
            limite.ligado, " limitar duração: de ", limite.min, " a ", limite.max, " s"),
          h("span", { style: "flex:1" }),
          assuntos.length ? h("a", { href: `#/p/${enc(projeto.nome)}/5` }, h("button", { class: "primario" }, "Exportar →")) : null),
        caixaVersoes,
        resumoLista,
        lista)));

  desenharTudo();  // (rAF não roda com a aba em segundo plano)
  const a0 = selecionado();
  if (a0) pFrase(frasesDo(a0)[0] ?? 0)?.scrollIntoView({ block: "center" });
}
