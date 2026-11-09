// Máquina de Cortes — interface em etapas. Estado no servidor (projeto.json); aqui só a tela.
const $ = (sel, el = document) => el.querySelector(sel);
const tela = $("#tela");
const ETAPAS = ["Entrada", "Assuntos", "Layouts", "B-roll", "Exportar", "Postar"];
let tarefa = { status: "livre" };
let projeto = null;      // projeto aberto
let salvarTimer = null;

async function api(metodo, url, corpo) {
  const r = await fetch(url, {
    method: metodo,
    headers: corpo instanceof FormData ? {} : { "Content-Type": "application/json" },
    body: corpo instanceof FormData ? corpo : corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    alert(e.detail || `Erro ${r.status}`);
    throw new Error(e.detail);
  }
  return r.json();
}

const h = (tag, attrs = {}, ...filhos) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f != null) el.append(f.nodeType ? f : document.createTextNode(f));
  return el;
};

// tempo: 3725.4 -> "1:02:05.4" ; aceita "1:02:05", "62:05", "3725"
const fmt = (s) => {
  s = Math.max(0, s);
  const hh = Math.floor(s / 3600), mm = Math.floor(s / 60) % 60, ss = (s % 60).toFixed(1).padStart(4, "0");
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
};
const lerTempo = (t) => t.trim().split(":").reduce((acc, p) => acc * 60 + parseFloat(p.replace(",", ".")), 0);
const dur = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);
const enc = encodeURIComponent;
// "3" = assunto principal; "3.2" = 2º corte dentro do assunto 3 (mesma regra de cortes.numero no servidor)
const numeroAssunto = (a, lista = projeto.assuntos) => {
  const topo = lista.filter((x) => !x.pai);
  if (!a.pai) return String(topo.indexOf(a) + 1);
  return `${topo.findIndex((x) => x.id === a.pai) + 1}.${lista.filter((x) => x.pai === a.pai).indexOf(a) + 1}`;
};

// ---------------------------------------------------------------- tarefa (barra no topo)

async function acompanharTarefa() {
  const antes = tarefa;
  tarefa = await fetch("/api/tarefa").then((r) => r.json());
  const box = $("#tarefa");
  box.hidden = tarefa.status === "livre";
  box.className = tarefa.status;
  $("#tarefa-progresso").style.width = `${tarefa.status === "rodando" ? tarefa.progresso || 2 : 100}%`;
  const rotulo = { ok: "Concluído: ", erro: "Erro: ", parada: "Parada: " }[tarefa.status] || "";
  $("#tarefa-texto").textContent = rotulo + (tarefa.status === "erro" ? (tarefa.log || []).slice(-1)[0] || tarefa.texto : tarefa.texto || "");
  $("#tarefa-parar").hidden = tarefa.status !== "rodando";
  if (antes.status === "rodando" && tarefa.status !== "rodando") {
    if (tarefa.status === "ok" && tarefa.tipo === "entrada" && tarefa.projeto && !location.hash.includes("/p/"))
      location.hash = `#/p/${enc(tarefa.projeto)}/2`;
    else rotear();
  }
  if ($("#log-dlg").open) mostrarLog();
  setTimeout(acompanharTarefa, tarefa.status === "rodando" ? 1000 : 3000);
}
$("#tarefa-parar").onclick = () => confirm("Parar a tarefa?") && api("POST", "/api/tarefa/parar");
function mostrarLog() {
  const pre = $("#log-txt");
  const noFim = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.textContent = (tarefa.log || []).join("\n") || "(sem saída ainda)";
  if (noFim) pre.scrollTop = pre.scrollHeight;
}
$("#tarefa-log-btn").onclick = () => { $("#log-dlg").showModal(); $("#log-txt").scrollTop = 1e9; mostrarLog(); };
const ocupado = () => tarefa.status === "rodando";
const ocupadoAqui = () => ocupado() && tarefa.projeto === projeto?.nome;

// ---------------------------------------------------------------- rotas

async function rotear() {
  limparEditor();                      // sai do editor: solta atalhos de teclado e salva
  limparEditor = () => {};
  if (location.hash.startsWith("#/config")) return telaConfig();
  const m = location.hash.match(/^#\/p\/([^/]+)\/(\d)/);
  if (!m) return inicio();
  projeto = await api("GET", `/api/projetos/${m[1]}`);
  $("#trilha").textContent = projeto.nome;
  const n = +m[2];
  tela.replaceChildren(abas(n), [null, etapaEntrada, etapaAssuntos, etapaLayouts, etapaBroll, etapaExportar, etapaPostar][n]());
}
window.addEventListener("hashchange", rotear);

function abas(atual) {
  const liberada = (n) => n === 1 || (projeto.arquivos.transcricao && (n === 2 || projeto.assuntos.length));
  return h("nav", { class: "etapas" }, ETAPAS.map((nome, i) =>
    h("a", { href: `#/p/${enc(projeto.nome)}/${i + 1}`, class: (i + 1 === atual ? "ativa " : "") + (liberada(i + 1) ? "" : "bloqueada") },
      `${i + 1}. ${nome}`)));
}

// ---------------------------------------------------------------- início: lista de lives

async function inicio() {
  projeto = null;
  $("#trilha").textContent = "";
  const lista = await api("GET", "/api/projetos");
  if (/^#\/(config|p\/)/.test(location.hash)) return;  // a rota mudou enquanto a lista carregava
  const url = h("input", { type: "text", placeholder: "Link da live (YouTube, Twitch, Kick...)" });
  const arquivo = h("input", { type: "file", accept: "video/*", hidden: true });
  arquivo.onchange = async () => {
    const fd = new FormData();
    fd.append("arquivo", arquivo.files[0]);
    await api("POST", "/api/projetos/upload", fd);
    rotear();
  };
  tela.replaceChildren(
    h("h1", {}, "Lives"),
    cartaoYoutube(),
    h("div", { class: "cartao" },
      h("h2", {}, "Nova live"),
      h("div", { class: "linha" }, url,
        h("button", { class: "primario", disabled: ocupado(), onclick: async () => { await api("POST", "/api/projetos/novo", { url: url.value }); url.value = ""; } }, "Baixar"),
        h("span", { class: "fraco" }, "ou"),
        h("button", { disabled: ocupado(), onclick: () => arquivo.click() }, "Enviar arquivo de vídeo"), arquivo),
      h("p", { class: "fraco pequeno" }, "Depois do download o sistema gera uma cópia leve para o editor e transcreve a live sozinho.")),
    h("div", { class: "projetos" }, lista.length ? lista.map((p) =>
      h("a", { class: "projeto", href: `#/p/${enc(p.nome)}/${Math.min(p.etapa || 1, 6)}` },
        h("span", { class: "nome" }, p.nome),
        h("span", { class: "fraco pequeno" }, new Date(p.data * 1000).toLocaleDateString("pt-BR")),
        p.assuntos ? h("span", { class: "selo" }, `${p.assuntos} assuntos`) : null,
        h("span", { class: "selo" }, `etapa ${p.etapa || 1}: ${ETAPAS[(p.etapa || 1) - 1]}`))) : h("p", { class: "fraco" }, "Nenhuma live ainda.")));
}

// Conta do YouTube: o download usa a sessão do navegador (cookies), não a senha. Sem ela o YouTube às vezes
// pede "confirme que não é um robô" e o download para.
let esperandoLogin = null;  // consulta periódica enquanto a janela de login do Firefox está aberta
function cartaoYoutube() {
  const card = h("div", { class: "cartao" });
  const arquivo = h("input", { type: "file", accept: ".txt", hidden: true });
  arquivo.onchange = async () => {
    const fd = new FormData();
    fd.append("arquivo", arquivo.files[0]);
    arquivo.value = "";
    desenhar(await api("POST", "/api/youtube/cookies", fd));
  };
  const entrar = async () => {
    await api("POST", "/api/youtube/entrar");
    desenhar({ conectado: false, firefox: true }, true);
    clearInterval(esperandoLogin);
    const fim = Date.now() + 10 * 60 * 1000;
    esperandoLogin = setInterval(async () => {
      const st = await fetch("/api/youtube").then((r) => r.json());
      if (st.conectado || Date.now() > fim || !card.isConnected) {
        clearInterval(esperandoLogin);
        if (card.isConnected) desenhar(st);
      }
    }, 3000);
  };
  const desenhar = (st, aguardando) => card.replaceChildren(...[
    h("div", { class: "linha" },
      h("h2", { style: "margin:0;flex:1" }, "Conta do YouTube"),
      st.conectado ? h("span", { class: "selo ok" }, st.como === "arquivo" ? "conectada (cookies.txt)" : "conectada") : h("span", { class: "selo" }, "sem conta"),
      st.conectado
        ? h("button", { class: "discreto", onclick: async () => desenhar(await api("DELETE", "/api/youtube")) }, "Desconectar")
        : h("button", { class: "primario", disabled: !st.firefox, onclick: entrar }, aguardando ? "Abrir a janela de novo" : "Entrar com minha conta")),
    aguardando ? h("p", { class: "pequeno" }, "Abri uma janela do Firefox na página de login. Entre na sua conta do YouTube por lá; " +
      "quando a sua foto aparecer no canto, pode fechar a janela. Esta página percebe sozinha.") : null,
    !st.firefox && !st.conectado ? h("p", { class: "pequeno fraco" }, "Para entrar com a conta, instale o Firefox (é o navegador que o Studio consegue usar para guardar a sessão).") : null,
    h("details", { class: "pequeno fraco" },
      h("summary", {}, "Para que serve"),
      h("p", {}, "Com a sua conta, o YouTube não interrompe o download com \"confirme que não é um robô\" e, no Premium, libera o 1080p de qualidade maior. " +
        "A velocidade vem de baixar vários pedaços ao mesmo tempo, com ou sem conta."),
      h("p", {}, "O login fica numa janela do Firefox só do Studio (separada do seu Firefox), salva neste computador em Viral Cutter/navegador_youtube. " +
        "Ela dá acesso à sua conta: não compartilhe a pasta Viral Cutter inteira. \"Desconectar\" apaga essa sessão."),
      h("p", {}, "Alternativa: ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); arquivo.click(); } }, "enviar um cookies.txt"),
        " exportado de um navegador logado no YouTube."),
      arquivo)].filter(Boolean));  // replaceChildren escreveria "null" no lugar dos itens vazios
  api("GET", "/api/youtube").then((st) => desenhar(st));
  return card;
}

// ---------------------------------------------------------------- 1. entrada

function etapaEntrada() {
  const a = projeto.arquivos;
  const item = (ok, texto) => h("div", { class: ok ? "sim" : "nao" }, texto);
  return h("div", { class: "cartao" },
    h("h2", {}, "Entrada"),
    h("div", { class: "checklist" },
      item(a.video, "Vídeo da live"), item(a.proxy, "Cópia leve para o editor (360p)"), item(a.transcricao, "Transcrição com tempo de cada palavra")),
    h("p", { class: "fraco pequeno" }, `Origem: ${projeto.origem || "—"}`),
    h("div", { class: "linha" },
      a.proxy && a.transcricao
        ? h("a", { href: `#/p/${enc(projeto.nome)}/2` }, h("button", { class: "primario" }, "Ir para os assuntos →"))
        : h("button", { class: "primario", disabled: ocupado(), onclick: () => api("POST", `/api/projetos/${enc(projeto.nome)}/entrada`, {}) },
            ocupadoAqui() ? "Processando..." : "Processar entrada"),
      a.transcricao ? h("button", { class: "discreto", disabled: ocupado(), title: "Para quando a transcrição saiu no idioma errado. O idioma é o das Configurações.",
        onclick: () => {
          if (projeto.assuntos.length && !confirm("Transcrever de novo muda o tempo das frases: os assuntos atuais deixam de bater com a fala e precisam ser gerados de novo. Continuar?")) return;
          api("POST", `/api/projetos/${enc(projeto.nome)}/entrada`, { retranscrever: true });
        } }, "Transcrever de novo") : null,
      h("a", { href: "#/config", class: "fraco pequeno" }, "idioma da transcrição em Configurações")));
}

// ---------------------------------------------------------------- 2. assuntos

// Só envia se houve edição local ainda não salva: senão sobrescreveria mudanças feitas no servidor
// (assuntos gerados pelo Claude, resumo) com a cópia antiga da tela.
let pendente = null;
function salvarAssuntos() {
  pendente = { nome: projeto.nome, assuntos: projeto.assuntos };
  clearTimeout(salvarTimer);
  salvarTimer = setTimeout(salvarPendente, 400);
}
function salvarPendente() {
  clearTimeout(salvarTimer);
  if (!pendente) return Promise.resolve();
  const { nome, assuntos } = pendente;
  pendente = null;
  return api("PUT", `/api/projetos/${enc(nome)}/assuntos`, assuntos);
}

// etapaAssuntos(): ver editor.js

// etapaLayouts(): ver layouts.js

// ---------------------------------------------------------------- 4. exportar

const selExport = {}, selFormatos = {};  // por live: assuntos e formatos marcados na etapa 4
function etapaExportar() {
  const incluidos = projeto.assuntos.filter((a) => a.incluir);
  const marcados = (selExport[projeto.nome] ||= new Set(incluidos.map((a) => a.id)));
  const sel = incluidos.filter((a) => marcados.has(a.id));
  const escolhidos = (selFormatos[projeto.nome] ||= new Set(projeto.exportacao?.formatos || ["legenda"]));
  const formato = (id, titulo, desc) => h("label", {},
    h("input", { type: "checkbox", checked: escolhidos.has(id), onchange: (e) => (e.target.checked ? escolhidos.add(id) : escolhidos.delete(id)) }),
    h("span", {}, h("b", {}, titulo), h("span", { class: "fraco pequeno" }, desc)));
  const formatos = h("div", { class: "formatos" },
    formato("legenda", "Vídeo com legenda", "Pronto para postar."),
    formato("sem_legenda", "Vídeo sem legenda", "Mesma montagem, sem legenda queimada."),
    formato("editavel", "Pacote editável (Premiere)", "XML com as camadas, legenda .srt e a mídia bruta numa pasta só. O fundo desfocado vai como efeito no XML."),
    h("div", { class: "subopcao" }, formato("fundo_renderizado", "Pré-renderizar o fundo desfocado",
      "Só para editores que não leem efeitos do XML (ex.: DaVinci Resolve). Deixa a exportação mais demorada.")));

  const exportar = (lista) => api("POST", `/api/projetos/${enc(projeto.nome)}/exportar`, {
    ids: lista.map((a) => a.id),
    formatos: [...escolhidos],
  });
  const marcar = (ids, sim) => { ids.forEach((id) => (sim ? marcados.add(id) : marcados.delete(id))); rotear(); };
  const feitos = new Set((projeto.exportacao?.cortes || []).filter((c) => !c.erro).map((c) => c.id));
  const assuntos = h("div", { class: "formatos" }, incluidos.map((a) =>
    h("label", { class: "linha" + (a.pai ? " filho" : "") },
      h("input", { type: "checkbox", checked: marcados.has(a.id), onchange: (e) => marcar([a.id], e.target.checked) }),
      h("span", { style: "flex:1" }, `${numeroAssunto(a)}. ${a.titulo}`),
      h("span", { class: "fraco pequeno" }, dur(a.fim - a.inicio)),
      feitos.has(a.id) ? h("span", { class: "selo ok" }, "exportado") : null,
      h("button", { class: "discreto", disabled: ocupado(), onclick: (e) => { e.preventDefault(); exportar([a]); } }, "Exportar só este"))));

  const nomes = Object.fromEntries(projeto.assuntos.map((a) => [a.id, a.titulo]));
  const tocar = (caminho) => {
    $("#video-dlg-player").src = `/media/${enc(projeto.nome)}/${caminho.split("/").map(enc).join("/")}`;
    $("#video-dlg").showModal();
  };
  const res = projeto.exportacao?.cortes || [];
  return h("div", {},
    h("div", { class: "cartao" },
      h("h2", {}, "Formatos"), formatos),
    cartaoLegenda(incluidos),
    h("div", { class: "cartao" },
      h("div", { class: "linha" }, h("h2", { style: "flex:1;margin:0" }, "Assuntos"),
        h("button", { class: "discreto", onclick: () => marcar(incluidos.map((a) => a.id), true) }, "Todos"),
        h("button", { class: "discreto", onclick: () => marcar(incluidos.map((a) => a.id), false) }, "Nenhum")),
      incluidos.length ? assuntos : h("p", { class: "fraco" }, "Nenhum assunto incluído na etapa 2."),
      h("div", { class: "linha" },
        h("button", { class: "primario", disabled: ocupado() || !sel.length, onclick: () => exportar(sel) },
          ocupadoAqui() && tarefa.tipo === "exportar" ? "Exportando..." : `Exportar ${sel.length} assunto(s)`),
        h("span", { class: "fraco pequeno" }, `${dur(sel.reduce((s, a) => s + a.fim - a.inicio, 0))} de vídeo · reexportar um assunto substitui só os arquivos dele`))),
    res.length ? h("div", { class: "cartao" },
      h("div", { class: "linha" }, h("h2", { style: "flex:1;margin:0" }, "Última exportação"),
        h("button", { onclick: () => api("POST", `/api/projetos/${enc(projeto.nome)}/abrir-pasta`, { sub: "entrega" }) }, "Abrir pasta"),
        h("a", { href: `#/p/${enc(projeto.nome)}/6` }, h("button", { class: "primario" }, "Postar →"))),
      res.map((c) => h("div", { class: "resultado" },
        h("span", { class: "nome" }, nomes[c.id] || c.pasta),
        c.erro ? h("span", { class: "selo", style: "color:var(--perigo)" }, "falhou") :
          c.arquivos.map((f) => f.endsWith("/")
            ? h("button", { onclick: () => api("POST", `/api/projetos/${enc(projeto.nome)}/abrir-pasta`, { sub: `${c.pasta}/editavel` }) }, "editável")
            : h("button", { onclick: () => tocar(`${c.pasta}/${f}`) }, f.includes("_sem_legenda") ? "▶ sem legenda" : "▶ com legenda"))))) : null);
}

acompanharTarefa();
rotear();
