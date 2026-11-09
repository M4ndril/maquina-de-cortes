import os
import re
import sys
import time

import yt_dlp
from i18n.i18n import I18nAuto
i18n = I18nAuto()

BROWSERS = ('chrome', 'edge', 'firefox', 'brave', 'opera', 'vivaldi', 'chromium', 'whale')


def _deno():
    """O YouTube exige resolver um desafio em JavaScript (obrigatório com conta logada; sem ele faltam formatos).
    Caminho explícito: um Studio aberto antes de instalar o Deno não tem o PATH novo."""
    import glob
    import shutil
    achado = shutil.which("deno") or next(iter(glob.glob(os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "DenoLand.Deno*", "deno.exe"))), None)
    return {'js_runtimes': {'deno': {'path': achado}}} if achado else {}


JS = _deno()

def fontes_de_sessao(sessoes=(), cookies_file=None):
    """Opções de cookies para o yt-dlp, na ordem em que são tentadas: sessões do Studio (conta do YouTube),
    cookies.txt, navegadores instalados e, por último, sem cookies."""
    return ([dict(s) for s in sessoes] + ([{'cookiefile': cookies_file}] if cookies_file else [])
            + [{'cookiesfrombrowser': (b,)} for b in BROWSERS] + [{}])


def download_with_browser_cookies(url, ydl_opts, fontes):
    """Baixa com a primeira fonte de sessão que o YouTube aceitar."""
    base = {k: v for k, v in ydl_opts.items() if k not in ('cookiefile', 'cookiesfrombrowser')}
    erro = None
    for fonte in fontes:
        try:
            print(f"Baixando com sessão: {fonte or 'sem cookies'}", flush=True)
            with yt_dlp.YoutubeDL({**base, **fonte}) as ydl:
                ydl.download([url])
            return
        except Exception as e:
            erro = e
            print(f"Sessão recusada ({fonte or 'sem cookies'}): {e}", flush=True)
    raise erro


def sanitize_filename(name):
    """Remove caracteres inválidos e emojis para evitar erro de encoding no Windows."""
    # Remove caracteres reservados do sistema de arquivos
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    
    # Remove emojis e caracteres não suportados pelo console Windows (CP1252)
    # Isso mantém acentos (á, ç, é) mas remove 😱, etc.
    try:
        cleaned = cleaned.encode('cp1252', 'ignore').decode('cp1252')
    except:
        # Fallback se não tiver CP1252: remove tudo não-ascii (remove acentos)
        cleaned = cleaned.encode('ascii', 'ignore').decode('ascii')
        
    cleaned = cleaned.strip()
    if len(cleaned) > 60:  # nome da pasta da live entra em todos os caminhos: longo demais passa dos 260 do Windows
        cleaned = cleaned[:60].rsplit(" ", 1)[0].rstrip(" ,.-")
    return cleaned

def andamento(d):
    """(fração 0-1, MB/s, segundos restantes) de um aviso de progresso do yt-dlp. Live vem em pedaços:
    a contagem de pedaços é exata, o tamanho total é só estimativa."""
    if d.get("fragment_count"):
        frac = (d.get("fragment_index") or 0) / d["fragment_count"]
    else:
        frac = (d.get("downloaded_bytes") or 0) / (d.get("total_bytes") or d.get("total_bytes_estimate") or 1)
    return min(frac, 1.0), (d.get("speed") or 0) / 1e6, d.get("eta")


def criar_progress_hook(ao_progredir=None):
    """Linha no log a cada 5 s (o yt-dlp avisaria várias vezes por segundo e encheria o log) e ao_progredir a cada 1 s."""
    ultimo = {"log": 0.0, "barra": 0.0}

    def hook(d):
        agora = time.time()
        if d["status"] == "finished":
            print(f"[download] arquivo concluído: {os.path.basename(d.get('filename', ''))}", flush=True)
            return
        if d["status"] != "downloading":
            return
        frac, mbs, eta = andamento(d)
        if agora - ultimo["log"] >= 5:
            ultimo["log"] = agora
            resta = f"{int(eta) // 60} min {int(eta) % 60:02d} s" if eta else "?"
            print(f"[download] {frac:.1%} a {mbs:.1f} MB/s - faltam {resta}", flush=True)
        if ao_progredir and agora - ultimo["barra"] >= 1:
            ultimo["barra"] = agora
            ao_progredir(frac, mbs, eta)
    return hook

def download(url, base_root="VIRALS", download_subs=True, quality="best", cookies_file=None, ao_progredir=None, sessoes=()):
    # 1. Extrair informações do vídeo para pegar o título
    # 1. Extrair informações do vídeo para pegar o título
    print(i18n("Extracting video information..."))
    title = None
    
    # ... (Keep existing title extraction logic) ...
    # Instead of repeating it effectively, I will rely on the diff to keep it or re-write it if I have to replace the whole block.
    # Since replace_file_content works on line ranges, I should be careful.
    # Let's assume I'm replacing the whole function body or significant parts.
    
    fontes = fontes_de_sessao(sessoes, cookies_file)
    for fonte in fontes[:-1]:  # sem cookies é a "Tentativa 2" logo abaixo
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, **JS, **fonte}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title')
            if title:
                break
        except Exception as e:
            try:
                print(i18n("Warning: Failed to extract info with cookies: {}").format(e))
            except UnicodeEncodeError:
                print(i18n("Warning: Failed to extract info with cookies: [Encoding Error in Message]"))

    # Tentativa 2: Sem cookies
    if not title:
        try:
             with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, **JS}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title')
        except Exception as e:
            try:
                print(i18n("Error getting video info (without cookies): {}").format(e))
            except UnicodeEncodeError:
                print(i18n("Error getting video info (without cookies): [Encoding Error in Message]"))

    # Fallback final
    if title:
        safe_title = sanitize_filename(title)
        try:
            print(i18n("Detected title: {}").format(title))
        except UnicodeEncodeError:
            # Fallback for Windows consoles that choke on Emojis
            clean_title = title.encode('ascii', 'replace').decode('ascii')
            print(i18n("Detected title: {}").format(clean_title))
    else:
        print(i18n("WARNING: Title could not be obtained. Using 'Unknown_Video'."))
        safe_title = i18n("Unknown_Video")

    # 2. Criar estrutura de pastas
    project_folder = os.path.join(base_root, safe_title)
    os.makedirs(project_folder, exist_ok=True)
    
    # Caminho final do vídeo
    output_filename = 'input' 
    output_path_base = os.path.join(project_folder, output_filename)
    final_video_path = f"{output_path_base}.mp4"

    # Verificação inteligente
    if os.path.exists(final_video_path):
        if os.path.getsize(final_video_path) > 1024: 
            try:
                print(i18n("Video already exists at: {}").format(final_video_path))
            except UnicodeEncodeError:
                print(i18n("Video already exists at: {}").format(final_video_path.encode('ascii', 'replace').decode('ascii')))
            print(i18n("Skipping download and reusing local file."))
            return final_video_path, project_folder
        else:
            print(i18n("Existing file found but seems corrupted/empty. Downloading again..."))
            try:
                os.remove(final_video_path)
            except:
                pass

    # Limpeza de temp
    temp_path = f"{output_path_base}.temp.mp4"
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except:
            pass

    # Mapeamento de Qualidade
    quality_map = {
        "best": 'bestvideo+bestaudio/best',
        "1080p": 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        "720p": 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        "480p": 'bestvideo[height<=480]+bestaudio/best[height<=480]'
    }
    selected_format = quality_map.get(quality, 'bestvideo+bestaudio/best')
    print(i18n("Configuring download quality: {} -> {}").format(quality, selected_format))

    ydl_opts = {
        'format': selected_format,
        # sem 'overwrites': download cancelado continua de onde parou (os .part ficam na pasta da live)
        'outtmpl': output_path_base, 
        'postprocessor_args': [
            '-movflags', 'faststart'
        ],
        'merge_output_format': 'mp4',
        # o YouTube manda a live em milhares de pedaços e limita a velocidade de cada conexão: 8 pedaços ao mesmo tempo
        'concurrent_fragment_downloads': 8,
        'progress_hooks': [criar_progress_hook(ao_progredir)],
        'noprogress': True,  # a linha de progresso do próprio yt-dlp vira dezenas de linhas por segundo no log
        # Opções de Legenda
        'writesubtitles': download_subs,
        'writeautomaticsub': download_subs,
        'subtitleslangs': ['pt.*', 'en.*', 'sp.*'], # Prioritize generic PT, EN, SP
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'skip_download': False,
        'quiet': False,
        'no_warnings': False,
        'force_ipv4': True,
        **JS,
    }
    if cookies_file:
        ydl_opts['cookiefile'] = cookies_file
    

    
    if download_subs:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegSubtitlesConvertor',
            'format': 'srt',
        }]

    try:
        print(i18n("Downloading video to: {}...").format(project_folder))
    except UnicodeEncodeError:
        print(i18n("Downloading video to: {}...").format(project_folder.encode('ascii', 'replace').decode('ascii')))
    
    # Tentativa 1: Com configuração original
    try:
        download_with_browser_cookies(url, ydl_opts, fontes)
    except yt_dlp.utils.DownloadError as e:
        error_str = str(e)
        if "No address associated with hostname" in error_str or "Failed to resolve" in error_str:
            print(i18n("\n[CRITICAL ERROR] Connection Failure: Could not access YouTube."))
            print(i18n("Check your internet connection or if there is any DNS block."))
            print(i18n("Details: {}").format(e))
            sys.exit(1)
        
        elif download_subs and ("Unable to download video subtitles" in error_str or "429" in error_str):
            print(i18n("\nWarning: Error downloading subtitles ({}).").format(e))
            print(i18n("Retrying ONLY the video (without subtitles)..."))
            
            ydl_opts['writesubtitles'] = False
            ydl_opts['writeautomaticsub'] = False
            ydl_opts['postprocessors'] = [p for p in ydl_opts.get('postprocessors', []) if 'Subtitle' not in p.get('key', '')]
            
            try:
                download_with_browser_cookies(url, ydl_opts, fontes)
            except Exception as e2:
                print(i18n("Fatal error on second attempt: {}").format(e2))
                raise
        elif "is not a valid URL" in error_str:
             print(i18n("Error: the entered link is not valid."))
             raise 
        else:
            print(i18n("Download error: {}").format(e))
            raise
    except Exception as e:
        print(i18n("Unexpected error: {}").format(e))
        raise

    # RENOMEAR LEGENDA PARA PADRÃO (input.vtt ou input.srt)
    # Se for VTT, converte para SRT para garantir compatibilidade.
    try:
        import glob
        # Pega a primeira que encontrar
        potential_subs = glob.glob(os.path.join(project_folder, "input.*.vtt")) + glob.glob(os.path.join(project_folder, "input.*.srt"))
        
        if potential_subs:
            best_sub = potential_subs[0]
            ext = os.path.splitext(best_sub)[1]
            new_name = os.path.join(project_folder, "input.srt") # Vamos padronizar tudo para .srt
            
            if ext.lower() == '.vtt':
                try:
                    print(i18n("Formatting complex VTT subtitle ({}) to clean SRT...").format(os.path.basename(best_sub)))
                except UnicodeEncodeError:
                    print(i18n("Formatting complex VTT subtitle ({}) to clean SRT...").format(os.path.basename(best_sub).encode('ascii', 'replace').decode('ascii')))
                try:
                    with open(best_sub, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    
                    srt_content = []
                    counter = 1
                    
                    seen_texts = set()
                    last_text = ""
                    
                    for line in lines:
                        clean_line = line.strip()
                        # Ignora Headers e Metadados do VTT/Youtube
                        if clean_line.startswith("WEBVTT") or \
                           clean_line.startswith("X-TIMESTAMP") or \
                           clean_line.startswith("NOTE") or \
                           clean_line.startswith("Kind:") or \
                           clean_line.startswith("Language:"):
                            continue
                        
                        if "-->" in clean_line:
                            # Parse Timestamp
                            parts = clean_line.split("-->")
                            start = parts[0].strip()
                            # Remove tags de posicionamento "align:start position:0%"
                            end = parts[1].strip().split(' ')[0] 
                            
                            def fix_time(t):
                                t = t.replace('.', ',')
                                if t.count(':') == 1: 
                                    t = "00:" + t
                                return t
                            
                            current_start = fix_time(start)
                            current_end = fix_time(end)
                            
                        elif clean_line:
                             # Texto: remover tags complexas <00:00:00.560><c> etc
                             # O YouTube usa formato karaoke. Ex: "Quanto<...> custa<...>"
                             # Precisamos do texto limpo.
                             text = re.sub(r'<[^>]+>', '', clean_line).strip()
                             
                             if not text: continue
                             
                             # Lógica para remover duplicatas do estilo "Roll-up" ou "Karaoke"
                             # O YouTube repete a linha anterior às vezes.
                             # Ex:
                             # 1: "Quanto custa"
                             # 2: "Quanto custa\nQuantos quilos"
                             
                             # Vamos pegar apenas a ULTIMA linha se tiver quebras
                             lines_in_text = text.split('\n')
                             final_line = lines_in_text[-1].strip()
                             
                             if not final_line: continue

                             # Filtro de duplicidade consecutivo
                             if final_line == last_text:
                                 continue
                             
                             # Evita blocos ultra curtos (glitch de 10ms) que repetem texto
                             # Mas aqui estamos processando texto.
                             
                             srt_content.append(f"{counter}\n")
                             srt_content.append(f"{current_start} --> {current_end}\n")
                             srt_content.append(f"{final_line}\n\n")
                             
                             last_text = final_line
                             counter += 1
                    
                    with open(new_name, 'w', encoding='utf-8') as f_out:
                        f_out.writelines(srt_content)
                    
                    try:
                        print(i18n("Subtitle converted and cleaned: {}").format(new_name))
                    except UnicodeEncodeError:
                        print(i18n("Subtitle converted and cleaned: {}").format(new_name.encode('ascii', 'replace').decode('ascii')))
                    try: os.remove(best_sub) 
                    except: pass
                    
                except Exception as e_conv:
                    print(i18n("Failed to convert VTT: {}. Keeping original.").format(e_conv))
                    # Fallback: rename apenas
                    new_name_fallback = os.path.join(project_folder, "input.vtt")
                    if os.path.exists(new_name_fallback) and new_name_fallback != best_sub:
                        try: os.remove(new_name_fallback)
                        except: pass
                    os.rename(best_sub, new_name_fallback)

            else:
                # Já é SRT, só renomeia
                if os.path.exists(new_name) and new_name != best_sub:
                    try: os.remove(new_name)
                    except: pass
                os.rename(best_sub, new_name)
                try:
                    print(i18n("SRT subtitle renamed to: {}").format(new_name))
                except UnicodeEncodeError:
                    print(i18n("SRT subtitle renamed to: {}").format(new_name.encode('ascii', 'replace').decode('ascii')))
            
            # Limpa sobras
            for extra in potential_subs[1:]:
                try: os.remove(extra)
                except: pass

    except Exception as e_ren:
        print(i18n("Error processing subtitles: {}").format(e_ren))

    return final_video_path, project_folder
