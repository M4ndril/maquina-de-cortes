# Máquina de Cortes

App local que transforma lives e vídeos longos em cortes verticais (TikTok / Reels / Shorts), em 5 etapas:

1. **Entrada** — baixa a live (link) ou recebe o arquivo, gera uma cópia leve para o editor e transcreve (WhisperX).
2. **Assuntos** — o Claude acha os assuntos completos; você ajusta na linha do tempo, resume e conversa com ele.
3. **Layouts** — detecção automática do layout e de quem fala, com correção manual. Fora da geometria do canal (podcast, vlog, entrevista), o recorte vertical segue quem fala.
4. **Exportar** — vídeo com legenda, sem legenda e pacote editável (XML do Premiere + .srt + mídia), com estilo e correção da legenda.
5. **Postar** — o Claude escolhe em qual conta cada corte vai (pelo tema da conta) e escreve título e descrição; você confere e posta (YouTube, TikTok, Instagram, Facebook; várias contas por plataforma).

## Precisa ter

- Placa NVIDIA (NVENC e CUDA), FFmpeg no PATH
- [Claude Code](https://claude.com/claude-code) instalado e logado (`claude` no PATH): é ele que escolhe e resume os assuntos

## Usar

- Instalar: `install_dependencies.bat`
- Abrir: `run_studio.bat` → http://127.0.0.1:7870

## Pastas

- `studio/` — o app (servidor, tarefas, interface)
- `scripts/` — download, transcrição, layout do canal, legenda, exportação para o Premiere
- `config_local.json` — criado pela tela ⚙ Configurações: idioma da transcrição, apps e contas de postagem (fora do git)
- `layout_canal.json` — geometria e ajustes da detecção de layout
- `legenda_canal.json` — estilo da legenda (editado na etapa 4)
- `fontes/` — fontes extras (.ttf/.otf) para a legenda
- `VIRALS/<live>/` — cada live: vídeo, transcrição, `projeto.json` e a entrega

Baseado no [ViralCutter](https://github.com/RafaelGodoyEbert/ViralCutter) (licença em `LICENSE`).
