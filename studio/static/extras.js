// Configurações e Postagem (etapa 5).
// Usa h(), api(), fmt(), enc(), $, tela, projeto, ocupado(), tarefa, numeroAssunto(), cartaoYoutube(), rotear() de app.js.

const PLATAFORMAS = { youtube: "YouTube Shorts", tiktok: "TikTok", instagram: "Instagram Reels", facebook: "Facebook Reels" };
const MODELOS_CLAUDE = ["sonnet", "opus", "haiku"];
const RETORNO = (app) => `http://localhost:7870/api/contas/retorno/${app}`;
// um app de desenvolvedor por empresa; cada login nele vira uma ou mais contas
const APPS_POSTAGEM = [
  { app: "google", titulo: "YouTube", plataformas: ["youtube"], id: "Client ID", segredo: "Client secret",
    passos: ["console.cloud.google.com: crie um projeto e ative a \"YouTube Data API v3\".",
      "Tela de consentimento OAuth: tipo Externo; adicione como usuário de teste cada conta Google que vai postar.",
      "Credenciais → Criar → ID do cliente OAuth → Aplicativo da Web, com o URI de redirecionamento abaixo.",
      "Enquanto o projeto não passar pela verificação do Google, os vídeos enviados pela API ficam privados no canal (você publica à mão no YouTube Studio).",
      "Mais de um canal: clique em Conectar uma vez para cada canal e escolha o canal na tela do Google."] },
  { app: "meta", titulo: "Instagram e Facebook", plataformas: ["instagram", "facebook"], id: "ID do app", segredo: "Chave secreta do app",
    passos: ["developers.facebook.com: crie um app do tipo Empresa e adicione os produtos \"Login do Facebook\" e \"Instagram\".",
      "Login do Facebook → Configurações: cadastre o URI de redirecionamento abaixo.",
      "Cada Instagram precisa ser conta profissional e estar ligado a uma Página do Facebook.",
      "No login, marque todas as Páginas e Instagrams que quer usar: cada um vira uma conta aqui.",
      "Em modo de desenvolvimento só funcionam as contas com função no app (administrador/testador); para outras, o app precisa de revisão da Meta."] },
  { app: "tiktok", titulo: "TikTok", plataformas: ["tiktok"], id: "Client key", segredo: "Client secret",
    passos: ["developers.tiktok.com: crie um app, plataforma Desktop, com os produtos \"Login Kit\" e \"Content Posting API\" (Direct Post ligado).",
      "Login Kit: cadastre o URI de redirecionamento abaixo. Escopos: user.info.basic e video.publish.",
      "Enquanto o app não for auditado pelo TikTok, só é possível postar como privado (visibilidade \"Privado\" na etapa de postagem).",
      "Mais de uma conta: clique em Conectar uma vez para cada conta (saia da conta anterior no TikTok antes)."] },
];

// ---------------------------------------------------------------- Configurações

async function telaConfig() {
  projeto = null;
  $("#trilha").textContent = "Configurações";
  const cfg = await api("GET", "/api/config");
  if (!location.hash.startsWith("#/config")) return;  // a rota mudou enquanto carregava
  const aviso = new URLSearchParams(location.hash.split("?")[1] || "").get("aviso");
  const salvar = async (mudancas) => { await api("PUT", "/api/config", mudancas); history.replaceState(null, "", "#/config"); telaConfig(); };

  // --- transcrição
  const idioma = h("select", { onchange: () => salvar({ idioma: idioma.value }) },
    [["pt", "Português"], ["en", "Inglês"], ["es", "Espanhol"], ["auto", "Detectar sozinho"]].map(([v, r]) => h("option", { value: v, selected: cfg.idioma === v }, r)));
  const cartaoTranscricao = h("div", { class: "cartao" },
    h("h2", {}, "Transcrição"),
    h("div", { class: "linha" }, h("b", {}, "Idioma falado nos vídeos"), idioma),
    h("p", { class: "fraco pequeno" }, "\"Detectar sozinho\" ouve só os primeiros 30 segundos: live que abre com vinheta ou música pode sair inteira no idioma errado. " +
      "Para refazer uma live já transcrita: etapa 1 → Transcrever de novo."));

  // --- postagem: apps e contas
  const cartaoApp = (info) => {
    const app = cfg.apps[info.app];
    const id = h("input", { type: "text", value: app.id, placeholder: info.id, style: "flex:1" });
    const segredo = h("input", { type: "password", autocomplete: "off", style: "flex:1", placeholder: app.segredo ? "salvo (preencha só para trocar)" : info.segredo });
    const contas = cfg.contas.filter((c) => info.plataformas.includes(c.plataforma));
    const pronto = app.id && app.segredo;
    return h("div", { class: "cartao" },
      h("div", { class: "linha" }, h("h2", { style: "margin:0;flex:1" }, info.titulo),
        h("span", { class: "selo" + (contas.length ? " ok" : "") }, contas.length ? `${contas.length} conta(s)` : "nenhuma conta"),
        h("button", { class: "primario", disabled: !pronto, title: pronto ? "Abre o login da plataforma" : "Salve o id e o segredo do app primeiro",
          onclick: () => { location.href = `/api/contas/conectar/${info.app}`; } }, contas.length ? "Conectar outra conta" : "Conectar conta")),
      contas.map((c) => {
        const tema = h("input", { type: "text", value: c.assuntos, style: "flex:1", placeholder: "tema desta conta (ex.: política e economia; futebol; cortes de humor)",
          onchange: () => api("PUT", "/api/config", { contas: [{ id: c.id, assuntos: tema.value }] }) });
        return h("div", { class: "linha" }, h("span", { class: "selo" }, PLATAFORMAS[c.plataforma]), h("b", {}, c.nome), tema,
          h("button", { class: "discreto", onclick: async () => { if (confirm(`Tirar a conta ${c.nome} do Studio?`)) { await api("DELETE", `/api/contas/${c.id}`); telaConfig(); } } }, "Remover"));
      }),
      h("details", { class: "pequeno", open: !pronto },
        h("summary", {}, "App de desenvolvedor (feito uma vez)"),
        h("ol", { class: "fraco" }, info.passos.map((p) => h("li", {}, p))),
        h("div", { class: "linha" }, h("span", { class: "fraco" }, "URI de redirecionamento:"), h("code", {}, RETORNO(info.app))),
        h("div", { class: "linha" }, id, segredo,
          h("button", { onclick: () => salvar({ apps: { [info.app]: { id: id.value, segredo: segredo.value } } }) }, "Salvar app"))));
  };

  tela.replaceChildren(...[
    h("h1", {}, "Configurações"),
    aviso ? h("div", { class: "cartao" }, h("b", {}, aviso)) : null,
    cartaoTranscricao,
    h("h2", {}, "Contas para postagem"),
    h("p", { class: "fraco pequeno" }, "O tema escrito em cada conta é o que o Claude usa para decidir em qual conta cada corte vai. " +
      "As chaves e os acessos ficam só neste computador (config_local.json, fora do git); a senha das contas nunca passa pelo Studio."),
    ...APPS_POSTAGEM.map(cartaoApp),
    h("h2", {}, "Download"),
    cartaoYoutube()].filter(Boolean));
}

// ---------------------------------------------------------------- 5. Postar

const selPostar = {};  // por live: cortes marcados para postar
function etapaPostar() {
  const raiz = h("div", {}, h("div", { class: "cartao fraco" }, "Carregando contas..."));
  const exportados = new Map((projeto.exportacao?.cortes || []).filter((c) => !c.erro && c.arquivos.some((f) => f.endsWith(".mp4"))).map((c) => [c.id, c]));
  const cortes = projeto.assuntos.filter((a) => exportados.has(a.id));
  const marcados = (selPostar[projeto.nome] ||= new Set(cortes.map((a) => a.id)));

  api("GET", "/api/config").then((cfg) => {
    const contas = cfg.contas;
    if (!cortes.length) return raiz.replaceChildren(h("div", { class: "cartao fraco" }, "Nenhum corte exportado em vídeo ainda. Exporte na etapa 4 (com ou sem legenda)."));
    if (!contas.length) return raiz.replaceChildren(h("div", { class: "cartao" },
      h("p", {}, "Nenhuma conta conectada ainda."), h("a", { href: "#/config" }, h("button", { class: "primario" }, "Conectar contas em Configurações"))));

    const modelo = h("select", {}, MODELOS_CLAUDE.map((m) => h("option", {}, m)));
    const visibilidade = h("select", {}, h("option", { value: "1" }, "Público"), h("option", { value: "" }, "Privado (só eu)"));
    const feita = (a, cid) => a.postagem?.feitas?.[cid];
    const aPostar = (a) => (a.postagem?.contas || []).filter((cid) => contas.some((c) => c.id === cid) && !feita(a, cid)?.ok);
    const salvar = (a) => api("PUT", `/api/projetos/${enc(projeto.nome)}/assuntos/${a.id}/postagem`,
      { titulo: a.postagem.titulo, descricao: a.postagem.descricao, contas: a.postagem.contas });
    const sugerir = async (lista, botao) => {
      botao.disabled = true; botao.textContent = "Claude escolhendo...";
      try { await api("POST", `/api/projetos/${enc(projeto.nome)}/postagem/sugerir`, { ids: lista.map((a) => a.id), modelo: modelo.value }); } finally { rotear(); }
    };
    const tocar = (a) => {
      const c = exportados.get(a.id), f = [...c.arquivos].filter((x) => x.endsWith(".mp4")).sort((x, y) => x.includes("_sem_legenda") - y.includes("_sem_legenda"))[0];
      $("#video-dlg-player").src = `/media/${enc(projeto.nome)}/${`${c.pasta}/${f}`.split("/").map(enc).join("/")}`;
      $("#video-dlg").showModal();
    };

    const cartaoCorte = (a) => {
      a.postagem ||= { titulo: a.titulo, descricao: "", contas: [], feitas: {} };
      const titulo = h("input", { type: "text", value: a.postagem.titulo, maxlength: 100, style: "flex:1", onchange: () => { a.postagem.titulo = titulo.value; salvar(a); } });
      const descricao = h("textarea", { rows: 2, style: "width:100%", placeholder: "descrição e hashtags", onchange: () => { a.postagem.descricao = descricao.value; salvar(a); } }, a.postagem.descricao);
      return h("div", { class: "cartao" },
        h("div", { class: "linha" },
          h("input", { type: "checkbox", checked: marcados.has(a.id), onchange: (e) => { e.target.checked ? marcados.add(a.id) : marcados.delete(a.id); resumo(); } }),
          h("b", {}, `${numeroAssunto(a)}.`), titulo,
          h("button", { class: "discreto", onclick: () => tocar(a) }, "▶ ver")),
        descricao,
        h("div", { class: "linha" }, contas.map((c) => {
          const f = feita(a, c.id);
          return h("label", { class: "conta-post", title: c.assuntos ? `tema: ${c.assuntos}` : "sem tema definido" },
            h("input", { type: "checkbox", checked: a.postagem.contas.includes(c.id), disabled: !!f?.ok, onchange: (e) => {
              a.postagem.contas = e.target.checked ? [...a.postagem.contas, c.id] : a.postagem.contas.filter((x) => x !== c.id);
              salvar(a); resumo();
            } }),
            h("span", { class: "fraco pequeno" }, PLATAFORMAS[c.plataforma]), " ", c.nome,
            f?.ok ? (f.link ? h("a", { class: "selo ok", href: f.link, target: "_blank", rel: "noopener" }, `postado ${f.data}`) : h("span", { class: "selo ok" }, `postado ${f.data}`))
              : f ? h("span", { class: "selo", style: "color:var(--perigo)", title: f.erro }, "falhou") : null);
        })),
        Object.values(a.postagem.feitas || {}).filter((f) => !f.ok).map((f) => h("p", { class: "pequeno", style: "color:var(--perigo)" }, f.erro)));
    };

    const semSugestao = cortes.filter((a) => !a.postagem);
    const bSugerir = h("button", { class: semSugestao.length ? "primario" : "", onclick: () => sugerir(semSugestao.length ? semSugestao : cortes.filter((a) => marcados.has(a.id)), bSugerir) },
      semSugestao.length ? `Claude: escolher contas e escrever textos (${semSugestao.length})` : "Refazer sugestão dos marcados");
    const bPostar = h("button", { class: "primario" });
    const resumo = () => {
      const sel = cortes.filter((a) => marcados.has(a.id)), n = sel.reduce((s, a) => s + aPostar(a).length, 0);
      bPostar.disabled = ocupado() || !n;
      bPostar.textContent = ocupado() && tarefa.tipo === "postar" ? "Postando..." : `Postar (${n} postagem${n === 1 ? "" : "ns"})`;
      bPostar.onclick = () => {
        const publico = !!visibilidade.value;
        if (!confirm(`Publicar ${n} postagem(ns) de ${sel.filter((a) => aPostar(a).length).length} corte(s) como ${publico ? "PÚBLICO" : "privado"}? Isso vai para as contas de verdade.`)) return;
        api("POST", `/api/projetos/${enc(projeto.nome)}/postar`, { ids: sel.map((a) => a.id), publico });
      };
    };
    const cartoes = cortes.map(cartaoCorte);  // cria a.postagem dos que ainda não têm, antes do resumo
    resumo();
    raiz.replaceChildren(
      h("div", { class: "cartao" },
        h("div", { class: "linha" }, bSugerir, h("label", { class: "fraco pequeno" }, "modelo ", modelo),
          h("span", { style: "flex:1" }), h("label", { class: "fraco pequeno" }, "visibilidade ", visibilidade), bPostar),
        h("p", { class: "fraco pequeno" }, "O Claude escolhe as contas pelo tema de cada uma (Configurações) e escreve título e descrição; você confere, ajusta e só então posta. " +
          "O que já foi postado numa conta não é postado de novo.")),
      ...cartoes);
  });
  return raiz;
}
