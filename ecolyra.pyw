"""Ecolyra — letras traduzidas em tempo real, que escutam a música (overlay para Windows).

Lê música/posição de qualquer player (Spotify, YouTube no navegador, Deezer, VLC...)
via Windows Media Controls, busca a letra
sincronizada (LRCLIB → NetEase), traduz e mostra num overlay com controles.

Atalhos (com o overlay em foco):
  Espaço          tocar / pausar
  Ctrl+← / Ctrl+→ música anterior / próxima
  + / -           adianta / atrasa a letra em 0,25 s   (0 zera)
  Ctrl + / Ctrl - adianta / atrasa 2 s
  (em vídeos a sincronia é automática: o ouvinte.py escuta o áudio e acha o trecho na letra)
  O               liga/desliga a frase original
  L               abre a letra completa
  I               cria imagem para postar (Stories / Feed / Quadrado)
  Esc             esconde na bandeja
  Botão direito   menu com todas as opções
"""
import asyncio
import colorsys
import ctypes
import functools
import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageTk
from winrt.windows.media import MediaPlaybackAutoRepeatMode
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions

APP = "Ecolyra"
PASTA = os.path.dirname(os.path.abspath(__file__))
CFG_ARQ = os.path.join(PASTA, "config.json")
CACHE = os.path.join(PASTA, "cache")
ICO = os.path.join(PASTA, "icone.ico")
PNG = os.path.join(PASTA, "icone.png")
INICIAR_LNK = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup", APP + ".lnk")
UA = {"User-Agent": "Ecolyra/3.0 (https://github.com/Davicjc/ecolyra)"}
os.makedirs(CACHE, exist_ok=True)

IDIOMAS = {"pt": "Português", "en": "English", "es": "Español", "fr": "Français",
           "it": "Italiano", "de": "Deutsch", "ja": "日本語", "ko": "한국어"}

# ---------------- Configuração ----------------
PADRAO = {"x": 2430, "y": 760, "largura": 900, "fonte": 22, "opacidade": 0.92, "idioma": "pt",
          "original": True, "proxima": True, "auto_esconder": True, "travado": False, "offsets": {}}


def carregar_cfg():
    cfg = dict(PADRAO)
    try:
        cfg.update(json.load(open(CFG_ARQ, encoding="utf-8")))
    except Exception:
        try:  # posição da versão anterior
            x, y = open(os.path.join(PASTA, "posicao.txt")).read().split(",")
            cfg["x"], cfg["y"] = int(x), int(y) - 60
        except Exception:
            pass
    return cfg


def salvar_cfg():
    try:
        json.dump(cfg, open(CFG_ARQ, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


cfg = carregar_cfg()

# ---------------- Estado compartilhado ----------------
estado = {"titulo": None, "artista": None, "duracao": 0.0, "pos": 0.0, "pos_ts": time.time(),
          "tocando": False, "shuffle": False, "repeat": 0, "ativo": False,
          "capa": None, "capa_v": 0, "fonte": "", "app_id": "", "fontes": [],
          "pode_shuffle": True, "pode_repeat": True, "sem_tempo": False}
letra = {"chave": None, "linhas": [], "trad": [], "status": "Aguardando uma música...",
         "auto": None, "sinc_status": ""}  # auto = offset achado ouvindo o áudio
lock = threading.Lock()
comandos = queue.Queue()   # comandos de mídia → thread do player
ui_fila = queue.Queue()    # pedidos da bandeja → thread da interface


# ---------------- Players (Windows Media Controls) ----------------
# Qualquer app que aparece nos controles de mídia do Windows funciona: Spotify, YouTube /
# YouTube Music no navegador, Deezer, VLC, Media Player...
APPS = [  # trecho do AppUserModelID → (nome, processo para o volume)
    ("spotify", "Spotify", "spotify.exe"), ("chrome", "Chrome", "chrome.exe"),
    ("msedge", "Edge", "msedge.exe"), ("308046b0af4a39cb", "Firefox", "firefox.exe"),
    ("firefox", "Firefox", "firefox.exe"), ("opera", "Opera", "opera.exe"),
    ("brave", "Brave", "brave.exe"), ("vivaldi", "Vivaldi", "vivaldi.exe"),
    ("deezer", "Deezer", "deezer.exe"), ("vlc", "VLC", "vlc.exe"),
    ("zunemusic", "Media Player", "Microsoft.Media.Player.exe"), ("tidal", "TIDAL", "tidal.exe"),
    ("amazon", "Amazon Music", "amazon music.exe"), ("applemusic", "Apple Music", "applemusic.exe"),
]


def info_app(app_id):
    low = (app_id or "").lower()
    for chave, nome, proc in APPS:
        if chave in low:
            return nome, proc
    nome = re.split(r"[!_.]", app_id or "?")[-1] or app_id
    return nome, None


escolhida = {"id": None}  # sessão que está sendo seguida (fica nela até outra começar a tocar)


async def achar_sessao(so_escolhida=False):
    mgr = await SessionManager.request_async()
    sessoes = list(mgr.get_sessions())
    por_id = {s.source_app_user_model_id: s for s in sessoes}
    estado["fontes"] = [(i, info_app(i)[0]) for i in por_id]
    fixa = cfg.get("fonte_fixa")
    if fixa:
        return por_id.get(fixa)
    if so_escolhida:
        return por_id.get(escolhida["id"])
    tocando = [s for s in sessoes if int(s.get_playback_info().playback_status) == 4]
    ids_tocando = [s.source_app_user_model_id for s in tocando]
    atual = por_id.get(escolhida["id"])
    if atual and escolhida["id"] in ids_tocando:
        s = atual
    elif tocando:
        cur = mgr.get_current_session()
        s = cur if cur and cur.source_app_user_model_id in ids_tocando else tocando[0]
    else:
        s = atual or mgr.get_current_session() or (sessoes[0] if sessoes else None)
    escolhida["id"] = s.source_app_user_model_id if s else None
    return s


def segundos(v):
    return v.total_seconds() if hasattr(v, "total_seconds") else v.duration / 1e7


async def ler_player(quer_capa):
    s = await achar_sessao()
    if not s:
        return None
    props = await s.try_get_media_properties_async()
    tl = s.get_timeline_properties()
    info = s.get_playback_info()
    try:
        atualizado = tl.last_updated_time.timestamp()
    except Exception:
        atualizado = time.time()
    capa = None
    if quer_capa and props.thumbnail:
        try:
            st = await props.thumbnail.open_read_async()
            buf = Buffer(st.size)
            capa = bytes(await st.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD))
        except Exception:
            pass
    ctl = info.controls
    return {"titulo": props.title, "artista": props.artist, "app_id": s.source_app_user_model_id,
            "duracao": segundos(tl.end_time), "pos": segundos(tl.position), "atualizado": atualizado,
            "tocando": int(info.playback_status) == 4,  # 4 = Playing
            "shuffle": bool(info.is_shuffle_active),
            "repeat": int(info.auto_repeat_mode) if info.auto_repeat_mode is not None else 0,
            "pode_shuffle": bool(ctl.is_shuffle_enabled), "pode_repeat": bool(ctl.is_repeat_enabled),
            "capa": capa}


async def executar(cmd, arg=None):
    s = await achar_sessao(so_escolhida=True)  # manda pro mesmo player que está na tela
    if not s:
        return
    if cmd == "pp":
        await s.try_toggle_play_pause_async()
    elif cmd == "proxima":
        await s.try_skip_next_async()
    elif cmd == "anterior":
        await s.try_skip_previous_async()
    elif cmd == "seek":
        await s.try_change_playback_position_async(int(arg * 10_000_000))
    elif cmd == "shuffle":
        await s.try_change_shuffle_active_async(arg)
    elif cmd == "repeat":
        await s.try_change_auto_repeat_mode_async(MediaPlaybackAutoRepeatMode(arg))


def loop_player():
    loop = asyncio.new_event_loop()
    capa_ate = 0.0
    pendente = None  # (chave, desde, app) — só busca a letra quando o título fica estável (pula anúncios rápidos)
    while True:
        try:
            while not comandos.empty():
                loop.run_until_complete(executar(*comandos.get_nowait()))
            r = loop.run_until_complete(ler_player(time.time() < capa_ate))
            if r and r["titulo"]:
                chave = (r["titulo"], r["artista"])
                nome, _ = info_app(r["app_id"])
                with lock:
                    nova = chave != (estado["titulo"], estado["artista"])
                    estado.update(titulo=r["titulo"], artista=r["artista"], duracao=r["duracao"],
                                  tocando=r["tocando"], shuffle=r["shuffle"], repeat=r["repeat"], ativo=True,
                                  pos=r["pos"], pos_ts=r["atualizado"] if r["tocando"] else time.time(),
                                  fonte=nome, app_id=r["app_id"], pode_shuffle=r["pode_shuffle"],
                                  pode_repeat=r["pode_repeat"], sem_tempo=not r["duracao"])
                    if nova:
                        estado["capa"] = None
                        estado["capa_v"] += 1
                    if r["capa"] and r["capa"] != estado["capa"]:
                        estado["capa"] = r["capa"]
                        estado["capa_v"] += 1
                if nova:
                    capa_ate = time.time() + 4  # a capa às vezes chega um pouco depois do título
                    letra.update(chave=chave, linhas=[], trad=[], status="Identificando a música...", auto=None, sinc_status="",
                                 via="", ident=None)
                    pendente = (chave, time.time(), r["app_id"])
                if pendente and time.time() - pendente[1] > 1.5 and letra["chave"] == pendente[0]:
                    threading.Thread(target=carregar_letra, args=(*pendente[0], r["duracao"], False, pendente[2]),
                                     daemon=True).start()
                    pendente = None
            else:
                estado["ativo"] = False
        except Exception as e:
            letra["status"] = f"Erro lendo o player: {e}"
        fim = time.time() + 0.4
        while time.time() < fim and comandos.empty():
            time.sleep(0.03)


def posicao_atual():
    with lock:
        if estado["tocando"]:
            return min(estado["pos"] + (time.time() - estado["pos_ts"]), estado["duracao"] or 1e9)
        return estado["pos"]


# ---------------- Volume (sessão de áudio do player) ----------------
def volume_app(app_id):
    proc = info_app(app_id)[1]
    if not proc:
        return None
    try:
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name().lower() == proc.lower():
                return s.SimpleAudioVolume
    except Exception:
        pass
    return None


# ---------------- Busca de letra ----------------
def parse_lrc(texto):
    linhas = []
    for l in texto.splitlines():
        tags = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", l)
        frase = re.sub(r"\[.*?\]", "", l).strip()
        for m, s in tags:
            linhas.append([int(m) * 60 + float(s), frase])
    return sorted(linhas, key=lambda x: x[0])


def limpar_titulo(titulo):
    """Tira '(feat. X)', '- Remastered', '[Explicit]' etc."""
    t = re.sub(r"\s*[\(\[][^\)\]]*(feat|ft\.|with|remaster|explicit|version|live|from)[^\)\]]*[\)\]]", "", titulo, flags=re.I)
    t = re.sub(r"\s+-\s+.*(remaster|version|live|edit|mix).*$", "", t, flags=re.I)
    return t.strip() or titulo


def normalizar(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --- títulos de vídeo (YouTube etc.): "Artista - Música (Official Video) [4K]" ---
LIXO = (r"official|oficial|video|vídeo|clipe|clip\b|audio|áudio|lyric|letra|legendad|tradu[cç]|visuali[sz]er|"
        r"\bm/?v\b|\bhd\b|\bhq\b|\b4k\b|1080p|remaster|explicit|ao vivo|prod\.|color coded|sub\s*esp|\bpt-?br\b|"
        r"animation|animated|anima[cç][aã]o|\bamv\b|\bpmv\b|visualizer|\bsped up\b|\bslowed\b|nightcore")


def tirar_feat(s):
    return re.sub(r"\s*[\(\[]?\b(ft|feat|featuring)\b\.?\s.*$", "", s, flags=re.I).strip()


def limpar_video(t):
    t = re.sub(r"[\(\[【][^\)\]】]*[\)\]】]", lambda m: "" if re.search(LIXO, m.group(0), re.I) else m.group(0), t)
    t = re.sub(r"\s+(official|oficial)\b.*$", "", t, flags=re.I)
    t = re.sub(r"#\S+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–—|/")


def limpar_canal(a):
    a = (a or "").strip()
    sem = re.sub(r"(vevo|\s*-\s*topic|\s+official|\s+oficial|\s+music|\s+tv)$", "", a, flags=re.I).strip()
    if sem != a and " " not in sem:  # "ImagineDragonsVEVO" → "Imagine Dragons"
        sem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", sem)
    return sem


def candidatos(titulo, artista, app_id=""):
    """(música, artista) a tentar, do mais provável pro menos."""
    if "spotify" in (app_id or "spotify").lower():
        return [(titulo, artista)]  # o Spotify já manda certinho
    t, a = limpar_video(titulo), limpar_canal(artista)
    c = []
    # pedaços que são só lixo ("Lyrics", "Official Video") saem
    partes = [p for p in re.split(r"\s+[-–—~]\s+|\s*[|｜]\s*", t)
              if re.sub(LIXO, "", p, flags=re.I).strip(" -|/()[]")]
    if len(partes) >= 2:
        e, d = (p.strip(" \"'“”‘’") for p in partes[:2])
        par = [(tirar_feat(d), tirar_feat(e)), (tirar_feat(e), tirar_feat(d))]
        if a and normalizar(a) and normalizar(a) in normalizar(d) and normalizar(a) not in normalizar(e):
            par.reverse()  # "Música - Artista"
        c += par
    q = re.match(r"^(.+?)\s+[\"“'‘「『](.+?)[\"”'’」』]", t)  # BTS 'Dynamite' MV
    if q:
        c.append((q.group(2).strip(), q.group(1).strip()))
    c.append((tirar_feat(t), a))
    # variações: sem nada entre colchetes/parênteses e só com o 1º artista ("CG5 × Pomni" → "CG5")
    extra = []
    for m, art in c:
        m2 = re.sub(r"\s*[\(\[【][^\)\]】]*[\)\]】]", "", m).strip()
        a2 = re.split(r"\s*(?:[×,&]|\bx\b|\band\b|\bwith\b|\be\b|\by\b)\s*", art, maxsplit=1, flags=re.I)[0].strip()
        extra += [(m2, art), (m2, a2), (m, a2)]
    c += extra
    vistos, saida = set(), []
    for m, art in c:
        k = (normalizar(m), normalizar(art))
        if m and k[0] and k not in vistos:
            vistos.add(k)
            saida.append((m, art))
    return saida


def buscar_livre(titulo, artista, dur):
    """Pesquisa o título do vídeo inteiro (sem colchetes) e só aceita resultado cujo nome da
    música E algum artista aparecem no título do vídeo / nome do canal — pega qualquer formato."""
    sem_parenteses = limpar_video(re.sub(r"[\(\[【「『][^\)\]】」』]*[\)\]】」』]", " ", titulo))
    # a busca do LRCLIB exige todas as palavras → tenta o título todo e cada pedaço sozinho
    pedacos = [p.strip(" \"'“”‘’") for p in re.split(r"\s+[-–—~]\s+|\s*[|｜]\s*|\s+[×x]\s+", sem_parenteses)]
    buscas = [re.sub(r"\s*(?:[×|｜~–—-]|\bx\b)\s*", " ", sem_parenteses)]
    buscas += sorted({p for p in pedacos if len(normalizar(p)) >= 3} - set(buscas), key=len, reverse=True)[:3]
    texto_tit = normalizar(titulo)
    texto_tudo = normalizar(f"{titulo} {artista} {limpar_canal(artista)}")
    for q in buscas:
        achado = _livre_melhor(lrclib_busca({"q": q}), texto_tit, texto_tudo, dur)
        if achado:
            return achado
    return None


def _livre_melhor(itens, texto_tit, texto_tudo, dur):
    bons = []
    for i in itens:
        if not i.get("syncedLyrics"):
            continue
        nome = normalizar(limpar_titulo(i.get("trackName", "")))
        if len(nome) < 3 or nome not in texto_tit:
            continue
        artistas = [normalizar(x) for x in re.split(r",|&|×|\bx\b|feat\.?|ft\.?", i.get("artistName", ""), flags=re.I)]
        if not any(len(x) >= 2 and x in texto_tudo for x in artistas):
            continue
        diff = abs((i.get("duration") or 0) - dur) if dur else 0
        if dur and diff > 60:
            continue
        bons.append((-len(nome), diff, i["syncedLyrics"]))  # nome mais completo, depois duração mais perto
    return min(bons)[2] if bons else None


def parecido(alvo, nome, estrito):
    if not alvo or not nome:
        return not alvo
    if alvo == nome:
        return True
    if alvo in nome or nome in alvo:
        return not estrito or min(len(alvo), len(nome)) / max(len(alvo), len(nome)) >= 0.5
    return False


def melhor_candidato(itens, titulo, dur, tol=8, estrito=False):
    """Escolhe o resultado sincronizado com título parecido e duração mais próxima."""
    alvo = normalizar(limpar_titulo(titulo))
    bons = []
    for i in itens:
        if not i.get("syncedLyrics"):
            continue
        if not parecido(alvo, normalizar(limpar_titulo(i.get("trackName", ""))), estrito):
            continue
        diff = abs((i.get("duration") or 0) - dur) if dur else 0
        if dur and diff > tol:
            continue
        bons.append((diff, i["syncedLyrics"]))
    return min(bons)[1] if bons else None


def lrclib_busca(params):
    try:
        r = requests.get("https://lrclib.net/api/search", headers=UA, timeout=10, params=params)
        return r.json() if r.ok else []
    except Exception:
        return []


def buscar_lrclib(titulo, artista, dur, tol=8, estrito=False, so_titulo=True):
    limpo = limpar_titulo(titulo)
    if artista and tol <= 8:
        try:
            r = requests.get("https://lrclib.net/api/get", headers=UA, timeout=10,
                             params={"track_name": titulo, "artist_name": artista, "duration": round(dur)})
            if r.ok and r.json().get("syncedLyrics"):
                return r.json()["syncedLyrics"]
        except Exception:
            pass
    # 1) título + artista  2) só o título (Spotify às vezes manda só o 1º cantor)
    #    3) busca livre com título + artista
    buscas = []
    if artista:
        buscas.append({"track_name": limpo, "artist_name": artista})
    if so_titulo:
        buscas.append({"track_name": limpo})
    if artista:
        buscas.append({"q": f"{limpo} {artista}"})
    for params in buscas:
        achado = melhor_candidato(lrclib_busca(params), titulo, dur, tol, estrito)
        if achado:
            return achado
    return None


def buscar_netease(titulo, artista, dur, tol=8, estrito=False):
    """Segunda fonte: NetEase Cloud Music (catálogo enorme, inclusive ocidental)."""
    limpo = limpar_titulo(titulo)
    cab = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"}
    try:
        r = requests.post("https://music.163.com/api/search/get", timeout=10, headers=cab,
                          data={"s": f"{limpo} {artista}".strip(), "type": 1, "limit": 15})
        musicas = r.json().get("result", {}).get("songs", []) or []
    except Exception:
        return None
    alvo = normalizar(limpo)
    achados = []
    for m in musicas:
        if not parecido(alvo, normalizar(limpar_titulo(m.get("name", ""))), estrito):
            continue
        diff = abs(m.get("duration", 0) / 1000 - dur) if dur else 0
        if dur and diff > tol:
            continue
        achados.append((diff, m["id"]))
    for _, mid in sorted(achados)[:3]:
        try:
            l = requests.get("https://music.163.com/api/song/lyric", timeout=10, headers=cab,
                             params={"id": mid, "lv": 1}).json()
            lrc = (l.get("lrc") or {}).get("lyric", "")
            if len(re.findall(r"\[\d+:\d+", lrc)) > 5:
                # remove créditos tipo "作词 : ..." do começo
                return "\n".join(x for x in lrc.splitlines() if not re.search(r"[一-鿿]\s*[:：]", x))
        except Exception:
            continue
    return None


def buscar_letra(titulo, artista, dur, app_id=""):
    if "spotify" in (app_id or "spotify").lower():
        return buscar_lrclib(titulo, artista, dur) or buscar_netease(titulo, artista, dur)
    # vídeo: clipe tem intro/final, então a duração bate menos; o título precisa bater mais
    achado = buscar_livre(titulo, artista, dur)
    if achado:
        return achado
    cands = candidatos(titulo, artista, app_id)
    for m, a in cands:
        if a:
            achado = buscar_lrclib(m, a, dur, 60, True, so_titulo=False)
            if achado:
                return achado
    for m, a in cands:
        if a:
            achado = buscar_netease(m, a, dur, 60, True)
            if achado:
                return achado
    for m, _ in cands:  # último recurso: só o título, com a duração mais rígida
        achado = buscar_lrclib(m, "", dur, 15, True)
        if achado:
            return achado
    return None


# ---------------- Sincronia pelo áudio ----------------
# Clipe do YouTube tem introdução, cenas faladas, final diferente... então o tempo da letra
# (feita pra versão de estúdio) não bate. O ouvinte.py escuta o som do PC, transcreve, e aqui
# a gente acha onde aquelas palavras estão na letra → diferença de tempo.
CJK = "぀-ヿ一-鿿가-힯"  # japonês, chinês, coreano: não têm espaço → compara caractere por caractere


def tokens(texto):
    """'Don't stop' → ['dont', 'stop'];  '無敵の笑顔' → ['無', '敵', 'の', '笑', '顔']"""
    import unicodedata
    t = unicodedata.normalize("NFKC", texto.lower())
    t = "".join("".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
                if ord(c) < 0x2000 else c for c in t)  # tira acento só do alfabeto latino
    return re.findall(rf"[{CJK}]|[a-z0-9]+", t.replace("'", "").replace("’", ""))


def palavra_norm(w):
    return "".join(tokens(w))


def palavras_da_letra(linhas):
    """[(tempo estimado, token, é_início_de_linha)] — dentro da linha o tempo é interpolado."""
    saida = []
    for i, (t, frase) in enumerate(linhas):
        ws = tokens(frase)
        if not ws:
            continue
        cjk = sum(len(w) == 1 and re.match(rf"[{CJK}]", w) is not None for w in ws) > len(ws) / 2
        prox = linhas[i + 1][0] if i + 1 < len(linhas) else t + 4
        dur = max(0.5, min(prox - t, len(ws) * (0.22 if cjk else 0.42)))
        for k, w in enumerate(ws):
            saida.append((t + dur * k / len(ws), w, k == 0))
    return saida


def tokens_ouvidos(palavras):
    """Palavras do Whisper [(ini, fim, texto)] → [(tempo, token)], repartindo o tempo entre os tokens."""
    saida = []
    for ini, fim, w in palavras:
        ts = tokens(w)
        for k, tk in enumerate(ts):
            saida.append((ini + (fim - ini) * k / max(1, len(ts)), tk))
    return saida


def parecida(a, b):
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4:
        return False
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def alinhar(linhas, palavras, pos_ini, anterior=None):
    """Palavras ouvidas (tempo relativo ao início da gravação, que corresponde a pos_ini no vídeo)
    → (offset, confiança). offset = tempo_na_letra − tempo_no_vídeo."""
    letra_w = palavras_da_letra(linhas)
    ouvidas = tokens_ouvidos(palavras)
    if len(ouvidas) < 3 or len(letra_w) < 3:
        return None, 0
    votos = []  # (offset, peso)
    # caractere solto (japonês etc.) casa por acaso mais fácil → exige sequência maior
    n = 5 if sum(len(w) == 1 for _, w, _ in letra_w) > len(letra_w) / 2 else 3
    for j in range(len(ouvidas) - n + 1):
        trecho = [w for _, w in ouvidas[j:j + n]]
        for k in range(len(letra_w) - n + 1):
            if all(parecida(trecho[x], letra_w[k + x][1]) for x in range(n)):
                peso = 2.0 if letra_w[k][2] else 1.0
                votos.append((letra_w[k][0] - (pos_ini + ouvidas[j][0]), peso))
    if not votos:
        return None, 0
    # agrupa offsets parecidos (±0,8 s); em empate (refrão repetido) fica o mais perto do anterior/0
    ref = anterior if anterior is not None else 0.0
    melhor = None
    for o, _ in votos:
        grupo = [(x, p) for x, p in votos if abs(x - o) <= 0.8]
        apoio = sum(p for _, p in grupo)
        media = sum(x * p for x, p in grupo) / apoio
        chave = (round(apoio, 1), -abs(media - ref))
        if melhor is None or chave > melhor[0]:
            melhor = (chave, media, apoio)
    return round(melhor[1], 2), melhor[2]


def letras_candidatas(titulo_video, canal, ident, dur):
    """Junta várias versões de letra possíveis (nome do Shazam, título do vídeo, texto entre 「」...)."""
    art = ident[1] if ident else limpar_canal(canal)
    buscas = []
    if ident:
        buscas += [{"q": f"{ident[0]} {ident[1]}"}, {"track_name": ident[0]}]
    for x in re.findall(r"[「『【\"“(\[]([^」』】\"”)\]]+)[」』】\"”)\]]", titulo_video):
        if not re.search(LIXO, x, re.I):
            buscas += [{"q": f"{x} {art}"}, {"track_name": x}]
    for m, a in candidatos(titulo_video, canal, "video")[:3]:
        buscas.append({"q": f"{m} {a}".strip()})
    vistos, saida = set(), []
    for q in buscas[:9]:
        for i in lrclib_busca(q):
            lrc = i.get("syncedLyrics")
            if not lrc or lrc[:300] in vistos:
                continue
            if dur and i.get("duration") and abs(i["duration"] - dur) > 90:
                continue
            vistos.add(lrc[:300])
            saida.append((lrc, f"{i.get('trackName')} — {i.get('artistName')}"))
    return saida[:20]


def escolher_versao(cands, ouvidos):
    """A versão cuja letra mais combina com o que foi ouvido (soma da confiança do alinhamento)."""
    melhor = None
    for lrc, nome in cands:
        linhas = parse_lrc(lrc)
        total = sum(alinhar(linhas, palavras, pos_ini)[1] for palavras, pos_ini in ouvidos)
        if melhor is None or total > melhor[0]:
            melhor = (total, lrc, nome)
    return melhor


def trocar_versao(chave, dur, ident, ouvidos):
    """A letra atual não combina com o que está sendo cantado → procura a versão certa."""
    try:
        r = escolher_versao(letras_candidatas(chave[0], chave[1], ident, dur), ouvidos)
        if os.environ.get("TRADUTOR_DEBUG"):
            print(f"sinc: versão escolhida pelo áudio: {r and r[2]} (pontos {r and r[0]})")
        if not r or r[0] < 4 or letra["chave"] != chave:
            return
        arq = arq_cache(*chave)
        dados = {"lrc": r[1], "trad": {}, "quando": time.time(), "via": "audio", "nome": r[2],
                 "ident": list(ident) if ident else None}
        json.dump(dados, open(arq, "w", encoding="utf-8"), ensure_ascii=False)
        letra.update(linhas=parse_lrc(r[1]), trad=[], via="audio", auto=None, status="Traduzindo...")
        ouvinte["reset"] = True  # sincroniza de novo com a letra nova
        traduzir_letra(chave, dados, arq)
    except Exception as e:
        if os.environ.get("TRADUTOR_DEBUG"):
            print("sinc erro versão:", repr(e))


ouvinte = {"proc": None, "falhas": 0, "reset": False}


def sinc_ligada():
    modo = cfg.get("sinc_audio", "videos")
    if modo == "off" or not os.path.exists(os.path.join(PASTA, "ouvinte.py")):
        return False
    return modo == "sempre" or "spotify" not in (estado["app_id"] or "").lower()


def iniciar_ouvinte():
    p = ouvinte["proc"]
    if p and p.poll() is None:
        return p
    p = subprocess.Popen([sys.executable, "-u", os.path.join(PASTA, "ouvinte.py")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                         creationflags=0x08000000 | 0x00004000)  # sem janela, prioridade abaixo do normal
    if '"pronto"' not in (p.stdout.readline() or ""):
        p.kill()
        raise RuntimeError("ouvinte não iniciou")
    ouvinte["proc"] = p
    return p


def parar_ouvinte():
    p = ouvinte["proc"]
    if p and p.poll() is None:
        p.kill()
    ouvinte["proc"] = None


def sinc_salva(chave, dur, valor=None):
    """Lê/grava o offset automático no cache da música (por duração: cada vídeo tem o seu)."""
    arq = arq_cache(*chave)
    try:
        dados = json.load(open(arq, encoding="utf-8"))
    except Exception:
        return None
    k = str(round(dur))
    if valor is None:
        return dados.get("sinc", {}).get(k)
    dados.setdefault("sinc", {})[k] = valor
    json.dump(dados, open(arq, "w", encoding="utf-8"), ensure_ascii=False)


def buscar_por_id(titulo, artista, dur):
    """Busca com o nome/artista que o Shazam reconheceu (limpos, confiáveis)."""
    return (buscar_lrclib(titulo, artista, dur, 60, True) or buscar_netease(titulo, artista, dur, 60, True)
            or buscar_lrclib(titulo, artista, 0, 8, True) or buscar_livre(f"{artista} - {titulo}", artista, dur))


def precisa_trocar_letra(sz):
    """O Shazam reconheceu outra música que não a do título do vídeo (ou não tinha letra)?"""
    if letra.get("via") in ("shazam", "audio"):  # já foi escolhida pelo áudio
        return False
    if not letra["linhas"]:
        return letra["status"].startswith("Sem letra")
    nome = normalizar(limpar_titulo(sz["titulo"]))
    return bool(nome) and nome not in normalizar(estado["titulo"] or "") and nome not in normalizar(letra.get("nome_achado", ""))


def loop_sincronia():
    """Escuta o áudio (vídeos e players que não são o Spotify) para: 1) reconhecer a música pelo
    Shazam quando o título não ajuda; 2) achar a diferença de tempo entre o vídeo e a letra.
    Shazam primeiro (rápido, exato, funciona em parte instrumental); Whisper de reserva/conferência."""
    chave_atual, travado, ests, divergentes = None, False, [], []
    proximo, ocioso_desde = 0.0, time.time()
    errou_shazam, tentou_id, conferido, salvo_checado = 0, False, False, False
    tentativas_conf, sem_casar, trocou_versao, ouvidos, chave_atual_versao = 0, 0, False, [], None
    idioma_ok, inicio_musica = None, time.time()
    while True:
        time.sleep(0.5)
        try:
            dur = estado["duracao"]
            ativo = (sinc_ligada() and estado["tocando"] and not estado["sem_tempo"] and letra["chave"]
                     and not 0 < dur < 35 and not letra["status"].startswith(("Identificando", "Aguardando")))
            if not ativo:
                if ouvinte["proc"] and time.time() - ocioso_desde > 300:
                    parar_ouvinte()  # libera a memória se ficou parado 5 min
                continue
            ocioso_desde = time.time()
            chave = letra["chave"]
            if chave != chave_atual or ouvinte["reset"]:
                chave_atual, ests, divergentes = chave, [], []
                errou_shazam, tentou_id, conferido, salvo_checado = 0, False, False, ouvinte["reset"]
                tentativas_conf, sem_casar = 0, 0
                if chave != chave_atual_versao:  # trocar de versão: uma vez por música
                    chave_atual_versao, trocou_versao, ouvidos, idioma_ok = chave, False, [], None
                    inicio_musica = time.time()
                ouvinte["reset"] = False
                letra["auto"], travado = None, False
                letra["sinc_status"] = "ouvindo"
                proximo = 0.0
            if letra["linhas"] and not salvo_checado:  # já sincronizou este vídeo antes?
                salvo_checado = True
                salvo = sinc_salva(chave, dur)
                if salvo is not None:
                    letra["auto"], travado, conferido = salvo, True, True
                    letra["sinc_status"] = ""
                    proximo = time.time() + 20
            if time.time() < proximo:
                continue
            if not letra["linhas"] and letra["status"].startswith("Buscando"):
                pass  # a busca pelo nome ainda está rodando; já vai ouvindo pra reconhecer
            elif not letra["linhas"] and tentou_id:
                proximo = time.time() + 2
                continue

            usar_whisper = bool(letra["linhas"]) and (errou_shazam >= 2 or (travado and not conferido))
            if usar_whisper and travado:
                tentativas_conf += 1
                if tentativas_conf > 3:  # parte instrumental demais pra conferir: confia no Shazam
                    conferido, usar_whisper = True, errou_shazam >= 2
            p = iniciar_ouvinte()
            with lock:
                pos0, ts0 = estado["pos"], estado["pos_ts"]
            p.stdin.write(json.dumps({"ouvir": 10 if usar_whisper else 7, "shazam": True,
                                      "whisper": usar_whisper, "idioma": idioma_ok}) + "\n")
            p.stdin.flush()
            resp = json.loads(p.stdout.readline() or "{}")
            ouvinte["falhas"] = 0
            if letra["chave"] != chave or "t_ini" not in resp or not estado["tocando"]:
                continue
            if abs((pos0 + time.time() - ts0) - posicao_atual()) > 0.6:
                continue  # pulou/pausou no meio: a medição não vale
            with lock:
                pos_ini = estado["pos"] + (resp["t_ini"] - estado["pos_ts"])
            sz = resp.get("shazam") if isinstance(resp.get("shazam"), dict) and "titulo" in resp["shazam"] else None
            off, conf, origem = None, 0, ""
            if sz:
                errou_shazam = 0
                letra["ident"] = (sz["titulo"], sz["artista"])
                buscando = not letra["linhas"] and letra["status"].startswith("Buscando")
                if not tentou_id and buscando and time.time() - inicio_musica < 8:
                    proximo = time.time() + 1  # dá um tempo pra busca pelo nome terminar antes de decidir
                    continue
                if not tentou_id and (buscando or precisa_trocar_letra(sz)):  # nome demorando/errado → usa o do Shazam
                    tentou_id = True
                    letra["status"] = f"Reconheci pelo áudio: {sz['titulo']} — {sz['artista']}. Buscando a letra..."
                    threading.Thread(target=carregar_letra, daemon=True,
                                     args=(*chave, dur, True, estado["app_id"], (sz["titulo"], sz["artista"]))).start()
                    proximo = time.time() + 2
                    continue
                if letra["linhas"]:
                    off, conf, origem = round(sz["offset"] - pos_ini, 2), 8, "shazam"
            else:
                errou_shazam += 1
                if not letra["linhas"]:
                    if letra["status"].startswith("Sem letra") and errou_shazam >= 3:
                        letra["status"] = f"Sem letra para \"{chave[0]}\" (não reconheci pelo áudio)"
                        proximo = time.time() + 30
                    continue
            if resp.get("palavras"):
                w_off, w_conf = alinhar(letra["linhas"], resp["palavras"], pos_ini, letra.get("auto"))
                if len(resp["palavras"]) >= 8:
                    ouvidos = (ouvidos + [(resp["palavras"], pos_ini)])[-3:]
                # cantaram bastante e nada bate com a letra → provavelmente é outra versão
                # (ex.: veio a letra em inglês e o clipe é em japonês) → escolhe a versão pelo áudio
                if (w_off is None or w_conf < 3) and len(resp["palavras"]) >= 12:
                    sem_casar += 1
                    if not trocou_versao and (sz or sem_casar >= 2):
                        trocou_versao = True
                        letra["sinc_status"] = "ouvindo"
                        threading.Thread(target=trocar_versao, daemon=True,
                                         args=(chave, dur, letra.get("ident"), list(ouvidos))).start()
                        proximo = time.time() + 3
                        continue
                if w_off is not None and w_conf >= 3:
                    conferido = True
                    idioma_ok = resp.get("idioma") or idioma_ok  # confirmado: não deixa a IA chutar de novo
                    if off is None:
                        off, conf, origem = w_off, w_conf, "whisper"
                    elif abs(w_off - off) > 1.5 and w_conf >= 6:
                        # a letra é de outra versão que a do Shazam: vale o que foi cantado
                        off, conf, origem = w_off, w_conf, "whisper"
            if os.environ.get("TRADUTOR_DEBUG"):
                print(f"sinc: off={off} conf={conf} via={origem} travado={travado} "
                      f"shazam={sz and sz['titulo']} palavras={len(resp.get('palavras') or [])}")
            if off is None:
                proximo = time.time() + (1 if not travado else 15)
                continue
            if letra["chave"] != chave or ouvinte["reset"]:
                continue  # a letra trocou enquanto ouvia (outra versão/música): recomeça
            if travado and letra.get("auto") is None:
                travado = False
            if not travado:
                ests.append(off)  # só aplica depois de confirmado (a letra espera até lá)
                concorda = len(ests) >= 2 and abs(ests[-1] - ests[-2]) <= (0.5 if origem == "shazam" else 0.8)
                if concorda or (origem == "whisper" and conf >= 6):
                    travado = True
                    letra["auto"] = round((ests[-1] + ests[-2]) / 2, 2) if concorda else off
                    letra["sinc_status"] = ""
                    sinc_salva(chave, dur, letra["auto"])
                    if abs(cfg["offsets"].get(f"{chave[0]}|{chave[1]}", 0)) >= 1:
                        cfg["offsets"].pop(f"{chave[0]}|{chave[1]}", None)  # ajuste manual grosso não precisa mais
                        salvar_cfg()
                    proximo = time.time() + (5 if not conferido else 30)
            elif abs(off - letra["auto"]) <= 1.0:
                divergentes = []
                letra["auto"] = off if origem == "shazam" else round(letra["auto"] * 0.7 + off * 0.3, 2)
                proximo = time.time() + 30
            else:  # clipe com cena no meio: confirma 2x antes de mudar
                divergentes.append(off)
                if len(divergentes) >= 2 and abs(divergentes[-1] - divergentes[-2]) <= 0.8:
                    letra["auto"] = round((divergentes[-1] + divergentes[-2]) / 2, 2)
                    sinc_salva(chave, dur, letra["auto"])
                    divergentes = []
                    proximo = time.time() + 30
        except Exception as e:
            ouvinte["falhas"] += 1
            letra["sinc_status"] = "erro"
            if os.environ.get("TRADUTOR_DEBUG"):
                print("sinc erro:", repr(e))
            parar_ouvinte()
            time.sleep(min(60, 5 * ouvinte["falhas"]))


# ---------------- Tradução ----------------
# Várias fontes em cadeia: se uma bloquear (429/captcha) ou falhar, passa pra próxima
# e a bloqueada fica de "castigo" por um tempo pra não piorar o bloqueio.
castigo = {}  # nome da fonte → até quando pular


def em_blocos(frases, limite=1500):
    bloco = []
    for f in frases:
        bloco.append(f)
        if sum(len(x) for x in bloco) > limite:
            yield bloco
            bloco = []
    if bloco:
        yield bloco


def checar(r):
    if r.status_code == 429 or "/sorry/" in r.url:
        raise RuntimeError("bloqueado (429)")
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.json()


def trad_google_gtx(frases, idioma):
    saida = []
    for b in em_blocos(frases):
        r = requests.post("https://translate.googleapis.com/translate_a/single",
                          params={"client": "gtx", "sl": "auto", "tl": idioma, "dt": "t"},
                          data={"q": "\n".join(b)}, timeout=15)
        partes = "".join(p[0] for p in checar(r)[0] if p[0]).split("\n")
        if len(partes) != len(b):
            raise RuntimeError("linhas desalinhadas")
        saida += partes
    return saida


def trad_google_dict(frases, idioma):
    """Outro endpoint do Google (extensão do Chrome): recebe lista e devolve lista alinhada."""
    saida = []
    for b in em_blocos(frases):
        r = requests.post("https://clients5.google.com/translate_a/t",
                          params={"client": "dict-chrome-ex", "sl": "auto", "tl": idioma},
                          data={"q": b}, timeout=15)
        j = checar(r)
        if len(b) == 1 and not isinstance(j, list):
            j = [j]
        partes = [p[0] if isinstance(p, list) else p for p in j]
        if len(partes) != len(b):
            raise RuntimeError("linhas desalinhadas")
        saida += partes
    return saida


def trad_mymemory(frases, idioma):
    """MyMemory: uma frase por vez (por isso fica por último)."""
    saida = []
    for f in frases:
        r = requests.get("https://api.mymemory.translated.net/get", timeout=15,
                         params={"q": f[:500], "langpair": f"Autodetect|{idioma}"})
        j = checar(r)
        if str(j.get("responseStatus")) != "200":
            raise RuntimeError(j.get("responseDetails") or "falhou")
        saida.append(j["responseData"]["translatedText"])
    return saida


FONTES = [("Google", trad_google_gtx), ("Google (alt)", trad_google_dict), ("MyMemory", trad_mymemory)]


def traduzir(frases, idioma):
    """Traduz as frases tentando cada fonte; só repete linhas únicas (refrão vai uma vez só)."""
    unicas = list(dict.fromkeys(f for f in frases if f))
    if not unicas:
        return [""] * len(frases)
    erros = []
    for nome, fn in FONTES:
        if time.time() < castigo.get(nome, 0):
            continue
        for tentativa in range(2):
            try:
                res = fn(unicas, idioma)
                mapa = dict(zip(unicas, (t.strip() for t in res)))
                return [mapa.get(f, "") if f else "" for f in frases]
            except Exception as e:
                bloqueio = "429" in str(e)
                if bloqueio or tentativa:
                    castigo[nome] = time.time() + (600 if bloqueio else 60)
                    erros.append(f"{nome}: {e}")
                    break
                time.sleep(1.5)
    raise RuntimeError("; ".join(erros) or "todas as fontes em espera")


def arq_cache(titulo, artista):
    return os.path.join(CACHE, hashlib.md5(f"{titulo}|{artista}".encode()).hexdigest() + ".json")


def carregar_letra(titulo, artista, dur, forcar=False, app_id="", ident=None):
    chave = (titulo, artista)
    if "spotify" not in (app_id or "spotify").lower() and 0 < dur < 35:
        letra["status"] = "Aguardando a música (anúncio?)"
        return
    if ident:  # o Shazam reconheceu a música pelo áudio (via já marca: a busca pelo nome não sobrescreve)
        letra.update(linhas=[], trad=[], auto=None, via="shazam",
                     status=f"Reconheci pelo áudio: {ident[0]} — {ident[1]}. Buscando a letra...")
    else:
        letra.update(linhas=[], trad=[], status=f"Buscando letra: {titulo}...")
    arq = arq_cache(titulo, artista)
    try:
        dados = {} if forcar else json.load(open(arq, encoding="utf-8"))
    except Exception:
        dados = {}
    try:
        # sem letra fica em cache só por 1 dia (pode aparecer depois)
        if "lrc" not in dados or (not dados["lrc"] and time.time() - dados.get("quando", 0) > 86400):
            if ident:
                dados = {"lrc": buscar_por_id(*ident, dur), "trad": {}, "quando": time.time(),
                         "via": "shazam", "ident": list(ident)}
            else:
                dados = {"lrc": buscar_letra(titulo, artista, dur, app_id), "trad": {}, "quando": time.time()}
                if letra["chave"] == chave and letra.get("via") in ("shazam", "audio"):
                    return  # enquanto buscava pelo nome, o áudio já resolveu
            json.dump(dados, open(arq, "w", encoding="utf-8"), ensure_ascii=False)
        if letra["chave"] != chave:
            return
        letra["via"] = dados.get("via", "titulo")
        if dados.get("ident"):
            letra["ident"] = tuple(dados["ident"])
        if not dados["lrc"]:
            if ident:
                letra["status"] = f"Sem letra sincronizada para \"{ident[0]}\" — {ident[1]}"
            elif sinc_ligada() and not dados.get("via"):
                letra["status"] = "Sem letra pelo nome do vídeo — reconhecendo a música pelo áudio..."
            else:
                letra["status"] = f"Sem letra sincronizada para \"{titulo}\""
            return
        letra["linhas"] = parse_lrc(dados["lrc"])
        traduzir_letra(chave, dados, arq)
    except Exception as e:
        letra["status"] = f"Erro: {e}"


def traduzir_letra(chave, dados=None, arq=None):
    """Traduz a letra carregada para o idioma escolhido (usa cache por idioma)."""
    arq = arq or arq_cache(*chave)
    if dados is None:
        dados = json.load(open(arq, encoding="utf-8"))
    idioma = cfg["idioma"]
    linhas = letra["linhas"]
    trad = dados.get("trad", {}).get(idioma)
    if not trad or len(trad) != len(linhas):
        letra["trad"] = []
        letra["status"] = "Traduzindo..."
        espera = 5
        while True:
            try:
                trad = traduzir([l[1] for l in linhas], idioma)
                break
            except Exception as e:
                # mostra o original enquanto isso e tenta de novo, até trocar de música/idioma
                letra["status"] = f"Tradução indisponível, tentando de novo em {espera}s"
                if os.environ.get("TRADUTOR_DEBUG"):
                    print("tradução falhou:", e)
                fim = time.time() + espera
                while time.time() < fim:
                    if letra["chave"] != chave or cfg["idioma"] != idioma:
                        return
                    time.sleep(0.5)
                espera = min(espera * 2, 120)
                letra["status"] = "Traduzindo..."
        dados.setdefault("trad", {})[idioma] = trad
        json.dump(dados, open(arq, "w", encoding="utf-8"), ensure_ascii=False)
    if letra["chave"] == chave and cfg["idioma"] == idioma:
        letra["trad"] = trad
        letra["status"] = ""


# ---------------- Imagem para postar ----------------
FORMATOS = {  # nome: (largura, altura, margem de segurança topo, base, tamanho inicial da letra)
    "Stories": (1080, 1920, 230, 300, 64),
    "Feed": (1080, 1350, 80, 80, 58),
    "Quadrado": (1080, 1080, 60, 60, 52),
}
FONTES_WIN = r"C:\Windows\Fonts"


@functools.lru_cache(maxsize=64)
def fonte(peso, tam, escrita="latin"):
    arqs = {"latin": {"b": "segoeuib.ttf", "s": "seguisb.ttf", "r": "segoeui.ttf"},
            "ja": {"b": "YuGothB.ttc", "s": "YuGothB.ttc", "r": "YuGothR.ttc"},
            "ko": {"b": "malgunbd.ttf", "s": "malgunbd.ttf", "r": "malgun.ttf"}}
    try:
        return ImageFont.truetype(os.path.join(FONTES_WIN, arqs[escrita][peso]), tam)
    except OSError:
        return ImageFont.truetype(os.path.join(FONTES_WIN, arqs["latin"][peso]), tam)


def fonte_para(texto, peso, tam):
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", texto):
        return fonte(peso, tam, "ja")
    if re.search(r"[\uac00-\ud7af]", texto):
        return fonte(peso, tam, "ko")
    return fonte(peso, tam)


def quebrar(texto, f, largura):
    linhas, atual = [], ""
    for p in texto.split():
        teste = f"{atual} {p}".strip()
        if atual and f.getlength(teste) > largura:
            linhas.append(atual)
            atual = p
        else:
            atual = teste
    return linhas + [atual] if atual else linhas


def capa_hd(titulo, artista):
    """Capa em alta (1200 px) pelo iTunes; fica em cache. None se não achar com segurança."""
    arq = os.path.join(CACHE, "capa_" + hashlib.md5(f"{titulo}|{artista}".encode()).hexdigest() + ".jpg")
    if os.path.exists(arq):
        return open(arq, "rb").read()
    alvo = normalizar(limpar_titulo(titulo))
    for termo in (f"{limpar_titulo(titulo)} {artista}", limpar_titulo(titulo)):
        try:
            r = requests.get("https://itunes.apple.com/search", timeout=8,
                             params={"term": termo, "entity": "song", "limit": 15})
            for i in r.json().get("results", []):
                nome = normalizar(limpar_titulo(i.get("trackName", "")))
                if alvo and (alvo in nome or nome in alvo) and i.get("artworkUrl100"):
                    img = requests.get(i["artworkUrl100"].replace("100x100bb", "1200x1200bb"), timeout=10)
                    if img.ok:
                        open(arq, "wb").write(img.content)
                        return img.content
        except Exception:
            continue
    return None


def cor_destaque(img):
    r, g, b = img.resize((1, 1), Image.LANCZOS).getpixel((0, 0))[:3]
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.45), max(v, 0.9))
    return int(r * 255), int(g * 255), int(b * 255)


def gerar_imagem(capa, titulo, artista, pares, formato, original_destaque=False):
    """pares = [(tradução, original), ...] → Image RGB no formato pedido."""
    W, H, seg_topo, seg_base, tam0 = FORMATOS[formato]
    try:
        arte = Image.open(BytesIO(capa)).convert("RGB")
    except Exception:
        arte = Image.open(PNG).convert("RGB")

    # fundo: capa ampliada, bem desfocada e escurecida
    fundo = ImageOps.fit(arte, (W // 3, H // 3), Image.LANCZOS).filter(ImageFilter.GaussianBlur(7))
    fundo = fundo.resize((W, H), Image.BICUBIC)
    fundo = ImageEnhance.Color(fundo).enhance(1.3)
    fundo = ImageEnhance.Brightness(fundo).enhance(0.9).convert("RGBA")
    grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: 10 + v * 80 // 255)
    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sombra.putalpha(grad)
    fundo.alpha_composite(sombra)

    m, p = 64, 56                 # margem do cartão e respiro interno
    cw = W - 2 * m
    tl = cw - 2 * p               # largura útil do texto
    acento = cor_destaque(arte)

    # cabeçalho: capa + nome da música + artista
    tc = 136
    f_tit = fonte_para(titulo, "b", 46)
    f_art = fonte_para(artista, "r", 34)
    tit_l = quebrar(titulo, f_tit, tl - tc - 30)
    if len(tit_l) > 2:
        tit_l = tit_l[:2]
        while f_tit.getlength(tit_l[1] + "…") > tl - tc - 30 and len(tit_l[1]) > 1:
            tit_l[1] = tit_l[1][:-1]
        tit_l[1] += "…"
    alt_cab = max(tc, len(tit_l) * 56 + 46)

    # letra: diminui a fonte até caber
    limite = H - seg_topo - seg_base - 2 * p - alt_cab - 56
    for tam in range(tam0, 25, -2):
        tam_sub = round(tam * 0.64)
        blocos, total = [], 0
        for trad, orig in pares:
            grande, pequeno = (orig, trad) if original_destaque else (trad or orig, orig)
            if not pequeno or normalizar(pequeno) == normalizar(grande):
                pequeno = ""
            fg = fonte_para(grande, "b", tam)
            fp = fonte_para(pequeno, "r", tam_sub)
            lg = quebrar(grande, fg, tl)
            lp = quebrar(pequeno, fp, tl) if pequeno else []
            h = len(lg) * round(tam * 1.22) + (8 + len(lp) * round(tam_sub * 1.3) if lp else 0)
            blocos.append((fg, lg, fp, lp, h))
            total += h
        total += round(tam * 0.85) * (len(blocos) - 1)
        if total <= limite:
            break

    ch = p + alt_cab + 56 + total + p
    cx, cy = m, seg_topo + max(0, (H - seg_topo - seg_base - ch) // 2)

    # cartão de vidro fosco
    mascara = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((cx, cy, cx + cw, cy + ch), 44, fill=255)
    vidro = ImageEnhance.Brightness(fundo.filter(ImageFilter.GaussianBlur(28))).enhance(0.6)
    fundo.paste(vidro, (0, 0), mascara)
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    d.rounded_rectangle((cx, cy, cx + cw, cy + ch), 44, fill=(255, 255, 255, 14), outline=(255, 255, 255, 60), width=2)

    # capa pequena com cantos arredondados
    mini = ImageOps.fit(arte, (tc, tc), Image.LANCZOS)
    mm = Image.new("L", (tc, tc), 0)
    ImageDraw.Draw(mm).rounded_rectangle((0, 0, tc, tc), 20, fill=255)
    fundo.paste(mini, (cx + p, cy + p), mm)
    tx = cx + p + tc + 30
    ty = cy + p + (alt_cab - (len(tit_l) * 56 + 46)) // 2
    for l in tit_l:
        d.text((tx, ty), l, font=f_tit, fill=(255, 255, 255, 255))
        ty += 56
    d.text((tx, ty + 2), artista, font=f_art, fill=(255, 255, 255, 175))

    # linha de destaque na cor da capa
    y = cy + p + alt_cab + 26
    d.rounded_rectangle((cx + p, y, cx + p + 90, y + 5), 3, fill=acento + (255,))
    y += 30

    for i, (fg, lg, fp, lp, h) in enumerate(blocos):
        for l in lg:
            d.text((cx + p, y), l, font=fg, fill=(255, 255, 255, 255))
            y += round(tam * 1.22)
        if lp:
            y += 8
            for l in lp:
                d.text((cx + p, y), l, font=fp, fill=(255, 255, 255, 165))
                y += round(tam_sub * 1.3)
        y += round(tam * 0.85)

    fundo.alpha_composite(camada)
    return fundo.convert("RGB")


def pasta_imagens():
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x27, None, 0, buf)  # Minhas imagens
    pasta = os.path.join(buf.value or os.path.expanduser("~\\Pictures"), APP)
    os.makedirs(pasta, exist_ok=True)
    return pasta


def copiar_imagem(img):
    """Coloca a imagem na área de transferência (cola direto no WhatsApp, Instagram web etc.)."""
    bmp = BytesIO()
    img.convert("RGB").save(bmp, "BMP")
    dados = bmp.getvalue()[14:]
    k32, u32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
    k32.GlobalAlloc.restype = k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    h = k32.GlobalAlloc(0x0002, len(dados))
    ctypes.memmove(k32.GlobalLock(h), dados, len(dados))
    k32.GlobalUnlock(h)
    if not u32.OpenClipboard(None):
        return False
    u32.EmptyClipboard()
    u32.SetClipboardData(8, h)  # CF_DIB
    u32.CloseClipboard()
    return True


# ---------------- Interface ----------------
BG = "#121212"
HOVER = "#2a2a2a"
BG2 = "#181818"  # barra de opções
VERDE = "#1DB954"
BRANCO = "#ffffff"
CINZA = "#b3b3b3"
APAGADO = "#6a6a6a"
ICONES = "Segoe Fluent Icons"
G = {"anterior": "", "proxima": "", "play": "", "pause": "",
     "shuffle": "", "rep": "", "rep1": "", "vol": "", "mudo": "",
     "cfg": "", "fechar": "", "min": "", "letra": "",
     "pin": "", "unpin": "",
     "globo": "\ue774", "original": "\ue8c1", "proxima_l": "\ue8fd", "tamanho": "\ue8e9",
     "largura": "\ue740", "opac": "\ue706", "ouvir": "\ue720", "fonte": "\ue8d6", "camera": "\ue722",
     "copiar": "\ue8c8", "buscar": "\ue72c", "olho": "\ue7b3", "ligar": "\ue7e8"}


def mmss(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class Dica:
    """Tooltip simples."""
    def __init__(self, w, texto):
        self.w, self.texto, self.tip, self.job = w, texto, None, None
        w.bind("<Enter>", self.agendar, add="+")
        w.bind("<Leave>", self.esconder, add="+")
        w.bind("<Button-1>", self.esconder, add="+")

    def agendar(self, e=None):
        self.job = self.w.after(500, self.mostrar)

    def mostrar(self):
        texto = self.texto() if callable(self.texto) else self.texto
        self.tip = tk.Toplevel(self.w)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=texto, bg="#282828", fg=BRANCO, font=("Segoe UI", 9),
                 padx=8, pady=4).pack()
        self.tip.geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 4}")

    def esconder(self, e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Botao(tk.Label):
    def __init__(self, pai, glifo, cmd, dica="", tam=12, fonte=ICONES):
        super().__init__(pai, text=glifo, font=(fonte, tam), bg=BG, fg=CINZA, cursor="hand2", padx=6, pady=4)
        self.ativo = False
        self.desligado = False  # o player não suporta (ex.: aleatório no YouTube)
        self.bind("<Enter>", lambda e: self.desligado or self.config(fg=BRANCO, bg=HOVER))
        self.bind("<Leave>", lambda e: self.config(fg=self.cor(), bg=BG))
        self.bind("<Button-1>", lambda e: self.desligado or cmd())
        if dica:
            Dica(self, dica)

    def cor(self):
        return "#3a3a3a" if self.desligado else VERDE if self.ativo else CINZA

    def set_ativo(self, a, desligado=False):
        if (a, desligado) != (self.ativo, self.desligado):
            self.ativo, self.desligado = a, desligado
            self.config(fg=self.cor(), cursor="arrow" if desligado else "hand2")


class App:
    HC = 56  # altura do cabeçalho
    HB = 4   # altura da barra de progresso
    HO = 34  # altura da barra de opções (ícones)

    def __init__(self):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ecolyra.App")
        except Exception:
            pass
        self.root = root = tk.Tk()
        root.title(APP)
        root.iconbitmap(default=ICO)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", cfg["opacidade"])
        root.configure(bg=BG)

        self.cab_visivel = True
        self.ultimo_dentro = time.time()
        self.menu_aberto = False
        self.capa_v = -1
        self.vol = None
        self.vol_tick = 0
        self.vol_app = None
        self.janela_letra = None
        self.idx_atual = -1

        self.f_trad = tkfont.Font(family="Segoe UI Semibold", size=cfg["fonte"])
        self.f_orig = tkfont.Font(family="Segoe UI", size=max(9, round(cfg["fonte"] * 0.62)))

        self.montar_cabecalho()
        self.ops = tk.Frame(root, bg=BG2, height=self.HO)
        self.ops.pack(fill="x")
        self.ops.pack_propagate(False)
        self.barra = tk.Canvas(root, height=self.HB, bg="#2a2a2a", highlightthickness=0, cursor="hand2")
        self.barra.pack(fill="x")
        self.barra.bind("<Button-1>", self.clicar_barra)
        Dica(self.barra, lambda: f"{mmss(posicao_atual())} / {mmss(estado['duracao'])} — clique para pular")

        self.corpo = tk.Frame(root, bg=BG)
        self.corpo.pack(fill="both", expand=True)
        self.l_orig = tk.Label(self.corpo, bg=BG, fg=CINZA, font=self.f_orig)
        self.l_trad = tk.Label(self.corpo, bg=BG, fg=BRANCO, font=self.f_trad)
        self.l_prox = tk.Label(self.corpo, bg=BG, fg=APAGADO, font=self.f_orig)
        self.l_orig.pack(fill="x", padx=16, pady=(10, 0))
        self.l_trad.pack(fill="both", expand=True, padx=16)
        self.l_prox.pack(fill="x", padx=16, pady=(0, 10))

        for w in (self.cab, self.info, self.capa, self.l_tit, self.l_art, self.ops,
                  self.corpo, self.l_orig, self.l_trad, self.l_prox):
            w.bind("<Button-1>", self.iniciar_arraste)
            w.bind("<B1-Motion>", self.arrastar)
            w.bind("<ButtonRelease-1>", self.soltar)
            w.bind("<Button-3>", self.abrir_menu)

        self.montar_menu()
        self.montar_opcoes()
        self.atalhos()
        self.aplicar_geometria()
        self.cantos_arredondados(root)
        self.iniciar_bandeja()
        self.atualizar()

    # ---------- montagem ----------
    def montar_cabecalho(self):
        self.cab = tk.Frame(self.root, bg=BG, height=self.HC)
        self.cab.pack(fill="x")
        self.cab.pack_propagate(False)

        icone = Image.open(PNG).resize((44, 44), Image.LANCZOS)
        self.img_padrao = ImageTk.PhotoImage(icone)
        self.capa = tk.Label(self.cab, image=self.img_padrao, bg=BG)
        self.capa.pack(side="left", padx=(8, 10), pady=6)

        dir_ = tk.Frame(self.cab, bg=BG)
        dir_.pack(side="right", padx=(0, 6))

        def sep():
            tk.Frame(dir_, width=1, height=22, bg="#333333").pack(side="left", padx=6)

        self.b_shuffle = Botao(dir_, G["shuffle"], self.alternar_shuffle, "Aleatório", 11)
        self.b_ant = Botao(dir_, G["anterior"], lambda: comandos.put(("anterior",)), "Anterior (Ctrl+←)")
        self.b_pp = Botao(dir_, G["play"], lambda: comandos.put(("pp",)), "Tocar / pausar (Espaço)", 16)
        self.b_prox = Botao(dir_, G["proxima"], lambda: comandos.put(("proxima",)), "Próxima (Ctrl+→)")
        self.b_rep = Botao(dir_, G["rep"], self.alternar_repeat, "Repetir", 11)
        for b in (self.b_shuffle, self.b_ant, self.b_pp, self.b_prox, self.b_rep):
            b.pack(side="left")
        sep()

        self.b_vol = Botao(dir_, G["vol"], self.alternar_mudo, lambda: f"Volume do {estado['fonte'] or 'player'} (roda do mouse)" + (" — muda o navegador inteiro" if estado["fonte"] in ("Chrome", "Edge", "Firefox", "Opera", "Brave", "Vivaldi") else ""), 11)
        self.b_vol.pack(side="left")
        self.c_vol = tk.Canvas(dir_, width=70, height=20, bg=BG, highlightthickness=0, cursor="hand2")
        self.c_vol.pack(side="left", padx=(0, 4))
        self.c_vol.bind("<Button-1>", self.mudar_volume)
        self.c_vol.bind("<B1-Motion>", self.mudar_volume)
        for w in (self.b_vol, self.c_vol):
            w.bind("<MouseWheel>", lambda e: self.passo_volume(0.05 if e.delta > 0 else -0.05))
        sep()

        Botao(dir_, "−", lambda: self.ajustar(-0.25), "Atrasar a letra 0,25 s (-)", 13, "Segoe UI").pack(side="left")
        self.l_sync = tk.Label(dir_, text="0,00s", bg=BG, fg=APAGADO, font=("Segoe UI", 9), width=9)
        self.l_sync.pack(side="left")
        self.l_sync.bind("<Button-1>", lambda e: self.ajustar(None))
        Dica(self.l_sync, self.dica_sinc)
        Botao(dir_, "+", lambda: self.ajustar(0.25), "Adiantar a letra 0,25 s (+)", 13, "Segoe UI").pack(side="left")
        sep()

        Botao(dir_, G["letra"], self.abrir_letra_completa, "Letra completa (L)", 11).pack(side="left")
        self.b_pin = Botao(dir_, G["unpin"], self.alternar_trava, "Travar posição", 11)
        self.b_pin.pack(side="left")
        Botao(dir_, G["cfg"], lambda: self.abrir_menu(None), "Configurações", 11).pack(side="left")
        Botao(dir_, G["min"], self.esconder, "Esconder na bandeja (Esc)", 10).pack(side="left")
        fechar = Botao(dir_, G["fechar"], self.sair, "Fechar", 10)
        fechar.pack(side="left")
        fechar.bind("<Enter>", lambda e: fechar.config(fg=BRANCO, bg="#c42b1c"))

        self.info = tk.Frame(self.cab, bg=BG)
        self.info.pack(side="left", fill="both", expand=True)
        self.l_tit = tk.Label(self.info, text=APP, bg=BG, fg=BRANCO, font=("Segoe UI Semibold", 10), anchor="w")
        self.l_tit.pack(fill="x", pady=(9, 0))
        self.l_art = tk.Label(self.info, text="", bg=BG, fg=CINZA, font=("Segoe UI", 9), anchor="w")
        self.l_art.pack(fill="x")

    def montar_opcoes(self):
        """Barra de ícones com as configurações à mão (as mesmas do menu do botão direito)."""
        self.itens_op = []
        esq = tk.Frame(self.ops, bg=BG2)
        esq.pack(side="left", padx=(6, 0))
        dir_ = tk.Frame(self.ops, bg=BG2)
        dir_.pack(side="right", padx=(0, 6))
        tamanhos = {16: "P", 22: "M", 28: "G", 36: "GG"}
        larguras = {700: "Estreita", 900: "Normal", 1100: "Larga", 1400: "Extra"}
        sinc = {"videos": "Vídeos", "sempre": "Sempre", "off": "Desligada"}

        def item(pai, glifo, acao, dica, texto=None, ativo=None):
            f = tk.Frame(pai, bg=BG2, cursor="hand2")
            f.pack(side="left", padx=1, pady=4)
            ic = tk.Label(f, text=glifo, font=(ICONES, 10), bg=BG2, fg=CINZA, padx=5)
            ic.pack(side="left")
            tx = tk.Label(f, text="", font=("Segoe UI", 9), bg=BG2, fg=CINZA) if texto else None
            if tx:
                tx.pack(side="left", padx=(0, 6))
            ws = [f, ic] + ([tx] if tx else [])

            def cor(bg):
                for w in ws:
                    w.config(bg=bg)
            for w in ws:
                w.bind("<Enter>", lambda e: cor(HOVER))
                w.bind("<Leave>", lambda e: cor(BG2))
                w.bind("<Button-1>", lambda e, wid=f: acao(wid))
            Dica(f, dica)
            self.itens_op.append((ic, tx, texto, ativo))

        def sep(pai):
            tk.Frame(pai, width=1, height=16, bg="#333333").pack(side="left", padx=5)

        def menu(m):
            return lambda w: self.popup(m, w)

        def alternar(chave, var, geo=False):
            return lambda w: (var.set(not var.get()), self.set_cfg(chave, var.get(), geo))

        item(esq, G["globo"], menu(self.m_idioma), "Idioma da tradução", lambda: cfg["idioma"].upper())
        item(esq, G["original"], alternar("original", self.v_orig), "Mostrar a frase original (O)",
             ativo=lambda: cfg["original"])
        item(esq, G["proxima_l"], alternar("proxima", self.v_prox, True), "Mostrar a próxima linha",
             ativo=lambda: cfg["proxima"])
        sep(esq)
        item(esq, G["tamanho"], menu(self.m_tamanho), "Tamanho da letra", lambda: tamanhos.get(cfg["fonte"], cfg["fonte"]))
        item(esq, G["largura"], menu(self.m_largura), "Largura da janela", lambda: larguras.get(cfg["largura"], cfg["largura"]))
        item(esq, G["opac"], menu(self.m_opac), "Opacidade", lambda: f"{int(cfg['opacidade'] * 100)}%")
        sep(esq)
        item(esq, G["ouvir"], menu(self.m_sinc), "Sincronia pelo áudio (reconhece a música e acerta o tempo em vídeos)",
             lambda: sinc.get(cfg.get("sinc_audio", "videos"), "Vídeos"), lambda: cfg.get("sinc_audio", "videos") != "off")
        item(esq, G["fonte"], menu(self.m_fonte), "De onde vem a música (automático ou um player fixo)",
             lambda: (info_app(cfg["fonte_fixa"])[0] if cfg.get("fonte_fixa") else f"Auto · {estado['fonte'] or '—'}"))
        item(dir_, G["camera"], lambda w: self.imagem_da_linha_atual(), "Criar imagem para postar (I)")
        item(dir_, G["copiar"], lambda w: self.copiar(), "Copiar a tradução atual")
        item(dir_, G["buscar"], lambda w: self.recarregar(), "Buscar a letra de novo")
        sep(dir_)
        item(dir_, G["olho"], alternar("auto_esconder", self.v_auto), "Esconder os controles quando o mouse sai",
             ativo=lambda: cfg["auto_esconder"])
        item(dir_, G["ligar"], lambda w: (self.v_inicio.set(not self.v_inicio.get()), self.alternar_inicio()),
             "Iniciar com o Windows", ativo=lambda: self.v_inicio.get())

    def atualizar_opcoes(self):
        for ic, tx, texto, ativo in self.itens_op:
            on = ativo() if ativo else None
            cor = VERDE if on else (APAGADO if on is False else CINZA)
            if ic.cget("fg") != cor:
                ic.config(fg=cor)
            if tx is not None:
                t = str(texto())
                if tx.cget("text") != t:
                    tx.config(text=t)

    def popup(self, m, w):
        self.menu_aberto = True
        try:
            m.tk_popup(w.winfo_rootx(), w.winfo_rooty() + w.winfo_height() + 2)
        finally:
            m.grab_release()
            self.menu_aberto = False
            self.ultimo_dentro = time.time()

    def montar_menu(self):
        r = self.root
        m = self.menu = tk.Menu(r, tearoff=0)
        self.v_idioma = tk.StringVar(value=cfg["idioma"])
        self.v_orig = tk.BooleanVar(value=cfg["original"])
        self.v_prox = tk.BooleanVar(value=cfg["proxima"])
        self.v_auto = tk.BooleanVar(value=cfg["auto_esconder"])
        self.v_trava = tk.BooleanVar(value=cfg["travado"])
        self.v_fonte = tk.IntVar(value=cfg["fonte"])
        self.v_larg = tk.IntVar(value=cfg["largura"])
        self.v_opac = tk.DoubleVar(value=cfg["opacidade"])
        self.v_inicio = tk.BooleanVar(value=os.path.exists(INICIAR_LNK))

        sub = tk.Menu(m, tearoff=0)
        for cod, nome in IDIOMAS.items():
            sub.add_radiobutton(label=nome, value=cod, variable=self.v_idioma, command=self.mudar_idioma)
        m.add_cascade(label="Idioma da tradução", menu=sub)
        self.m_idioma = sub
        m.add_checkbutton(label="Mostrar frase original\tO", variable=self.v_orig,
                          command=lambda: self.set_cfg("original", self.v_orig.get()))
        m.add_checkbutton(label="Mostrar próxima linha", variable=self.v_prox,
                          command=lambda: self.set_cfg("proxima", self.v_prox.get(), True))
        m.add_checkbutton(label="Esconder controles automaticamente", variable=self.v_auto,
                          command=lambda: self.set_cfg("auto_esconder", self.v_auto.get()))
        m.add_checkbutton(label="Travar posição", variable=self.v_trava,
                          command=lambda: self.set_cfg("travado", self.v_trava.get()))
        m.add_separator()

        sub = tk.Menu(m, tearoff=0)
        for nome, v in (("Pequeno", 16), ("Médio", 22), ("Grande", 28), ("Enorme", 36)):
            sub.add_radiobutton(label=nome, value=v, variable=self.v_fonte,
                                command=lambda: self.set_cfg("fonte", self.v_fonte.get(), True))
        m.add_cascade(label="Tamanho da letra", menu=sub)
        self.m_tamanho = sub
        sub = tk.Menu(m, tearoff=0)
        for nome, v in (("Estreita", 700), ("Normal", 900), ("Larga", 1100), ("Extra larga", 1400)):
            sub.add_radiobutton(label=nome, value=v, variable=self.v_larg,
                                command=lambda: self.set_cfg("largura", self.v_larg.get(), True))
        m.add_cascade(label="Largura", menu=sub)
        self.m_largura = sub
        sub = tk.Menu(m, tearoff=0)
        for v in (1.0, 0.92, 0.8, 0.65, 0.5):
            sub.add_radiobutton(label=f"{int(v * 100)}%", value=v, variable=self.v_opac,
                                command=lambda: self.set_cfg("opacidade", self.v_opac.get(), True))
        m.add_cascade(label="Opacidade", menu=sub)
        self.m_opac = sub
        m.add_separator()

        sub = tk.Menu(m, tearoff=0)
        sub.add_command(label="Adiantar 0,25 s\t+", command=lambda: self.ajustar(0.25))
        sub.add_command(label="Atrasar 0,25 s\t-", command=lambda: self.ajustar(-0.25))
        sub.add_command(label="Adiantar 2 s\tCtrl +", command=lambda: self.ajustar(2))
        sub.add_command(label="Atrasar 2 s\tCtrl -", command=lambda: self.ajustar(-2))
        sub.add_command(label="Zerar\t0", command=lambda: self.ajustar(None))
        m.add_cascade(label="Sincronia da letra", menu=sub)
        sub = tk.Menu(m, tearoff=0)
        self.v_sinc = tk.StringVar(value=cfg.get("sinc_audio", "videos"))
        for nome, v in (("Só vídeos (YouTube etc.)", "videos"), ("Sempre (inclui Spotify)", "sempre"),
                        ("Desligada", "off")):
            sub.add_radiobutton(label=nome, value=v, variable=self.v_sinc, command=lambda v=v: self.mudar_sinc(v))
        sub.add_separator()
        sub.add_command(label="Sincronizar de novo agora", command=self.ressincronizar)
        m.add_cascade(label="Sincronia pelo áudio", menu=sub)
        self.m_sinc = sub
        self.m_fonte = tk.Menu(m, tearoff=0, postcommand=self.montar_menu_fonte)
        m.add_cascade(label="Fonte da música", menu=self.m_fonte)
        m.add_command(label="Letra completa\tL", command=self.abrir_letra_completa)
        m.add_command(label="Criar imagem para postar\tI", command=self.imagem_da_linha_atual)
        m.add_command(label="Copiar tradução atual", command=self.copiar)
        m.add_command(label="Buscar a letra de novo", command=self.recarregar)
        m.add_separator()
        m.add_checkbutton(label="Iniciar com o Windows", variable=self.v_inicio, command=self.alternar_inicio)
        m.add_command(label="Esconder na bandeja\tEsc", command=self.esconder)
        m.add_command(label="Fechar", command=self.sair)

    def montar_menu_fonte(self):
        """Automático (segue o que estiver tocando) ou fixa num player específico."""
        mf = self.m_fonte
        mf.delete(0, "end")
        fixa = cfg.get("fonte_fixa")
        self.v_fonte_fixa = tk.StringVar(value=fixa or "")

        def escolher(app_id):
            cfg["fonte_fixa"] = app_id or None
            salvar_cfg()

        mf.add_radiobutton(label="Automático (o que estiver tocando)", value="", variable=self.v_fonte_fixa,
                           command=lambda: escolher(None))
        ids = dict(estado["fontes"])
        if fixa and fixa not in ids:
            ids[fixa] = info_app(fixa)[0] + " (fechado)"
        if ids:
            mf.add_separator()
        for app_id, nome in ids.items():
            mf.add_radiobutton(label=nome, value=app_id, variable=self.v_fonte_fixa,
                               command=lambda a=app_id: escolher(a))

    def atalhos(self):
        r = self.root
        r.bind("<space>", lambda e: comandos.put(("pp",)))
        r.bind("<Control-Right>", lambda e: comandos.put(("proxima",)))
        r.bind("<Control-Left>", lambda e: comandos.put(("anterior",)))
        for k in ("<plus>", "<KP_Add>", "<equal>"):
            r.bind(k, lambda e: self.ajustar(0.25))
        for k in ("<minus>", "<KP_Subtract>"):
            r.bind(k, lambda e: self.ajustar(-0.25))
        for k in ("<Control-plus>", "<Control-KP_Add>", "<Control-equal>"):
            r.bind(k, lambda e: self.ajustar(2))
        for k in ("<Control-minus>", "<Control-KP_Subtract>"):
            r.bind(k, lambda e: self.ajustar(-2))
        r.bind("0", lambda e: self.ajustar(None))
        r.bind("o", lambda e: (self.v_orig.set(not self.v_orig.get()), self.set_cfg("original", self.v_orig.get())))
        r.bind("l", lambda e: self.abrir_letra_completa())
        r.bind("i", lambda e: self.imagem_da_linha_atual())
        r.bind("<Escape>", lambda e: self.esconder())
        r.bind("<Button-3>", self.abrir_menu)

    @staticmethod
    def cantos_arredondados(janela, escuro=False):
        try:
            janela.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(janela.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
            if escuro:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

    # ---------- geometria / arraste ----------
    def altura_letra(self):
        h = 20 + self.f_trad.metrics("linespace") * 2 + 6
        h += self.f_orig.metrics("linespace") * (2 if cfg["proxima"] else 1)
        return h

    def aplicar_geometria(self):
        w = cfg["largura"]
        for l in (self.l_orig, self.l_trad, self.l_prox):
            l.config(wraplength=w - 40)
        hl = self.altura_letra()
        topo = self.HC + self.HO + self.HB
        if self.cab_visivel:
            self.root.geometry(f"{w}x{topo + hl}+{cfg['x']}+{cfg['y']}")
        else:
            self.root.geometry(f"{w}x{hl}+{cfg['x']}+{cfg['y'] + topo}")

    def mostrar_cabecalho(self, sim):
        if sim == self.cab_visivel:
            return
        self.cab_visivel = sim
        if sim:
            self.cab.pack(fill="x", before=self.corpo)
            self.ops.pack(fill="x", before=self.corpo)
            self.barra.pack(fill="x", before=self.corpo)
        else:
            self.cab.pack_forget()
            self.ops.pack_forget()
            self.barra.pack_forget()
        self.aplicar_geometria()

    def iniciar_arraste(self, e):
        self.root.focus_force()
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def arrastar(self, e):
        if not cfg["travado"]:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def soltar(self, e):
        if cfg["travado"]:
            return
        cfg["x"] = self.root.winfo_x()
        cfg["y"] = self.root.winfo_y() - (0 if self.cab_visivel else self.HC + self.HO + self.HB)
        salvar_cfg()

    # ---------- ações ----------
    def set_cfg(self, chave, valor, geometria=False):
        cfg[chave] = valor
        salvar_cfg()
        if chave == "fonte":
            self.f_trad.configure(size=valor)
            self.f_orig.configure(size=max(9, round(valor * 0.62)))
        if chave == "opacidade":
            self.root.attributes("-alpha", valor)
        if geometria:
            self.aplicar_geometria()

    def chave_musica(self):
        return f"{estado['titulo']}|{estado['artista']}"

    def offset_manual(self):
        return cfg["offsets"].get(self.chave_musica(), 0.0)

    def offset(self):
        return self.offset_manual() + (letra.get("auto") or 0.0)

    def ajustar(self, d):
        k = self.chave_musica()
        novo = 0.0 if d is None else round(self.offset_manual() + d, 2)
        if novo:
            cfg["offsets"][k] = novo
        else:
            cfg["offsets"].pop(k, None)
        salvar_cfg()

    def esperando_sincronia(self):
        """Só mostra a letra andando depois que o áudio confirmou o tempo. Exceções: você mesmo
        ajustou (+/-) ou o ouvinte está falhando — aí mostra do jeito que der."""
        return (sinc_ligada() and letra.get("auto") is None and not self.offset_manual()
                and ouvinte["falhas"] < 3)

    def dica_sinc(self):
        a, m = letra.get("auto"), self.offset_manual()
        if not sinc_ligada():
            base = "Sincronia pelo áudio desligada para este player"
        elif a is None:
            base = "Ouvindo o áudio para sincronizar a letra..." if letra["linhas"] else "Sincronia pelo áudio"
        else:
            base = f"Sincronizado pelo áudio: {a:+.2f} s".replace(".", ",")
        if m:
            base += f"  ·  ajuste manual {m:+.2f} s".replace(".", ",")
        return base + "\nClique para zerar o ajuste manual"

    def mudar_sinc(self, modo):
        cfg["sinc_audio"] = modo
        salvar_cfg()
        if modo == "off":
            letra["auto"] = None
            parar_ouvinte()
        else:
            ouvinte["reset"] = True

    def ressincronizar(self):
        letra["auto"] = None
        letra["sinc_status"] = "ouvindo"
        ouvinte["reset"] = True

    def alternar_shuffle(self):
        comandos.put(("shuffle", not estado["shuffle"]))

    def alternar_repeat(self):
        # desligado → repetir playlist → repetir faixa → desligado
        comandos.put(("repeat", {0: 2, 2: 1, 1: 0}[estado["repeat"]]))

    def alternar_trava(self):
        self.v_trava.set(not cfg["travado"])
        self.set_cfg("travado", self.v_trava.get())

    def mudar_idioma(self):
        cfg["idioma"] = self.v_idioma.get()
        salvar_cfg()
        if letra["linhas"] and letra["chave"]:
            threading.Thread(target=traduzir_letra, args=(letra["chave"],), daemon=True).start()

    def recarregar(self):
        if letra["chave"]:
            threading.Thread(target=carregar_letra, args=(*letra["chave"], estado["duracao"], True, estado["app_id"]),
                             daemon=True).start()

    def copiar(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.l_trad.cget("text"))

    def alternar_inicio(self):
        if self.v_inicio.get():
            pyw = sys.executable.replace("python.exe", "pythonw.exe").replace("python3", "pythonw3")
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{INICIAR_LNK}');"
                  f"$s.TargetPath='{pyw}';$s.Arguments='\"{os.path.abspath(__file__)}\"';"
                  f"$s.WorkingDirectory='{PASTA}';$s.IconLocation='{ICO}';$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000)
        else:
            try:
                os.remove(INICIAR_LNK)
            except OSError:
                pass

    def abrir_menu(self, e):
        self.menu_aberto = True
        try:
            x = e.x_root if e else self.root.winfo_pointerx()
            y = e.y_root if e else self.root.winfo_pointery()
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()
            self.menu_aberto = False
            self.ultimo_dentro = time.time()

    def clicar_barra(self, e):
        dur = estado["duracao"]
        if dur:
            alvo = max(0, min(1, e.x / max(1, self.barra.winfo_width()))) * dur
            with lock:
                estado["pos"], estado["pos_ts"] = alvo, time.time()
            comandos.put(("seek", alvo))

    # ---------- volume ----------
    def obter_vol(self):
        if self.vol is None:
            self.vol = volume_app(estado["app_id"])
        return self.vol

    def mudar_volume(self, e):
        v = self.obter_vol()
        if v:
            try:
                v.SetMasterVolume(max(0.0, min(1.0, e.x / 70)), None)
                v.SetMute(0, None)
            except Exception:
                self.vol = None
            self.desenhar_volume()

    def passo_volume(self, d):
        v = self.obter_vol()
        if v:
            try:
                v.SetMasterVolume(max(0.0, min(1.0, v.GetMasterVolume() + d)), None)
            except Exception:
                self.vol = None
            self.desenhar_volume()

    def alternar_mudo(self):
        v = self.obter_vol()
        if v:
            try:
                v.SetMute(0 if v.GetMute() else 1, None)
            except Exception:
                self.vol = None
            self.desenhar_volume()

    def desenhar_volume(self):
        v = self.obter_vol()
        c = self.c_vol
        c.delete("all")
        try:
            nivel, mudo = (v.GetMasterVolume(), v.GetMute()) if v else (0, True)
        except Exception:
            self.vol, nivel, mudo = None, 0, True
        c.create_line(4, 10, 66, 10, fill="#4d4d4d", width=4, capstyle="round")
        x = 4 + 62 * (0 if mudo else nivel)
        c.create_line(4, 10, x, 10, fill=CINZA if mudo else VERDE, width=4, capstyle="round")
        c.create_oval(x - 5, 5, x + 5, 15, fill=BRANCO, outline="")
        self.b_vol.config(text=G["mudo"] if mudo else G["vol"])

    # ---------- bandeja ----------
    def iniciar_bandeja(self):
        try:
            import pystray
        except ImportError:
            self.bandeja = None
            return
        M = pystray.MenuItem
        menu = pystray.Menu(
            M("Mostrar / esconder", lambda: ui_fila.put("alternar"), default=True),
            pystray.Menu.SEPARATOR,
            M("Tocar / pausar", lambda: comandos.put(("pp",))),
            M("Próxima", lambda: comandos.put(("proxima",))),
            M("Anterior", lambda: comandos.put(("anterior",))),
            pystray.Menu.SEPARATOR,
            M("Fechar", lambda: ui_fila.put("sair")))
        self.bandeja = pystray.Icon("ecolyra", Image.open(PNG), APP, menu)
        self.bandeja.run_detached()

    def esconder(self):
        self.root.withdraw()

    def mostrar(self):
        self.root.deiconify()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.lift()

    def sair(self):
        parar_ouvinte()
        if self.bandeja:
            self.bandeja.stop()
        self.root.destroy()

    # ---------- letra completa ----------
    def abrir_letra_completa(self):
        if self.janela_letra and self.janela_letra.winfo_exists():
            self.janela_letra.lift()
            return
        j = self.janela_letra = tk.Toplevel(self.root)
        j.title(f"Letra — {APP}")
        j.geometry(f"560x760+{max(0, cfg['x'] + 20)}+80")
        j.configure(bg=BG)
        j.attributes("-topmost", True)

        # barra de baixo: escolher linhas → criar imagem para postar
        barra = tk.Frame(j, bg="#181818")
        barra.pack(side="bottom", fill="x")
        j.l_sel = tk.Label(barra, text="Clique nas linhas para criar uma imagem (Shift = intervalo)",
                           bg="#181818", fg=APAGADO, font=("Segoe UI", 9), anchor="w")
        j.l_sel.pack(side="left", fill="x", expand=True, padx=(16, 4), pady=10)
        j.b_img = tk.Label(barra, text="  Criar imagem  ", bg="#2a2a2a", fg=APAGADO,
                           font=("Segoe UI Semibold", 10), padx=10, pady=5)
        j.b_img.pack(side="right", padx=(4, 12))
        j.b_img.bind("<Button-1>", lambda e: self.criar_imagem())
        j.b_limpar = tk.Label(barra, text="Limpar", bg="#181818", fg=APAGADO, font=("Segoe UI", 9), cursor="hand2")
        j.b_limpar.pack(side="right")
        j.b_limpar.bind("<Button-1>", lambda e: self.selecionar(None))

        txt = tk.Text(j, bg=BG, fg=CINZA, bd=0, wrap="word", padx=24, pady=20,
                      font=("Segoe UI", 11), cursor="hand2", highlightthickness=0)
        sb = tk.Scrollbar(j, command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        txt.tag_config("titulo", font=("Segoe UI Semibold", 15), foreground=BRANCO)
        txt.tag_config("sub", font=("Segoe UI", 10), foreground=APAGADO)
        txt.tag_config("trad", font=("Segoe UI Semibold", 12), foreground=BRANCO)
        txt.tag_config("orig", font=("Segoe UI", 10), foreground=APAGADO)
        txt.tag_config("atual", foreground=VERDE)
        txt.tag_config("escolhida", background="#1f3a29")
        txt.bind("<Button-1>", self.clicar_linha)
        txt.bind("<MouseWheel>", lambda e: setattr(j, "mexeu", time.time()), add="+")
        j.txt, j.versao, j.marcada, j.chave = txt, None, None, None
        j.selec, j.ancora, j.mexeu, j.posicoes = set(), None, 0.0, []
        self.cantos_arredondados(j, escuro=True)

    def clicar_linha(self, e):
        j = self.janela_letra
        j.mexeu = time.time()
        pos = j.txt.index(f"@{e.x},{e.y}")
        for i, (ini, fim) in enumerate(getattr(j, "posicoes", [])):
            if j.txt.compare(pos, ">=", ini) and j.txt.compare(pos, "<=", fim):
                if i >= len(letra["linhas"]) or not letra["linhas"][i][1]:
                    break  # linha instrumental (♪)
                if e.state & 1 and j.ancora is not None:  # Shift: intervalo
                    a, b = sorted((j.ancora, i))
                    j.selec |= {k for k in range(a, b + 1) if letra["linhas"][k][1]}
                else:
                    j.selec ^= {i}
                    j.ancora = i
                self.selecionar(j.selec)
                break
        return "break"

    def selecionar(self, selec):
        j = self.janela_letra
        if not (j and j.winfo_exists()):
            return
        j.selec = set(selec or ())
        if not j.selec:
            j.ancora = None
        j.txt.tag_remove("escolhida", "1.0", "end")
        for i in j.selec:
            if i < len(j.posicoes):
                j.txt.tag_add("escolhida", *j.posicoes[i])
        n = len(j.selec)
        j.l_sel.config(text=f"{n} linha{'s' if n > 1 else ''} escolhida{'s' if n > 1 else ''}" if n else
                       "Clique nas linhas para criar uma imagem (Shift = intervalo)",
                       fg=BRANCO if n else APAGADO)
        j.b_img.config(bg=VERDE if n else "#2a2a2a", fg="#000000" if n else APAGADO,
                       cursor="hand2" if n else "arrow")

    def imagem_da_linha_atual(self):
        """Menu: abre a letra completa já com a linha que está tocando escolhida."""
        self.abrir_letra_completa()
        self.atualizar_letra_completa(self.idx_atual)
        if 0 <= self.idx_atual < len(letra["linhas"]) and letra["linhas"][self.idx_atual][1]:
            self.janela_letra.ancora = self.idx_atual
            self.selecionar({self.idx_atual})

    def criar_imagem(self):
        j = self.janela_letra
        if not (j and j.selec):
            return
        pares = [(letra["trad"][i] if i < len(letra["trad"]) else "", letra["linhas"][i][1])
                 for i in sorted(j.selec) if i < len(letra["linhas"])]
        titulo, artista, capa = estado["titulo"] or "", estado["artista"] or "", estado["capa"]
        if letra.get("ident") and "spotify" not in (estado["app_id"] or "").lower():
            titulo, artista = letra["ident"]  # nome certinho reconhecido pelo áudio, não o título do vídeo
        j.l_sel.config(text="Buscando a capa em alta...", fg=BRANCO)

        def buscar():
            hd = capa_hd(titulo, artista)
            ui_fila.put(("compartilhar", (hd or capa, titulo, artista, pares)))
        threading.Thread(target=buscar, daemon=True).start()

    def abrir_compartilhar(self, capa, titulo, artista, pares):
        self.selecionar(self.janela_letra.selec if self.janela_letra else ())
        j = tk.Toplevel(self.root)
        j.title(f"Imagem para postar — {APP}")
        j.configure(bg=BG)
        j.attributes("-topmost", True)
        j.resizable(False, False)
        estado_img = {"formato": "Stories", "orig": False, "img": None}

        prev = tk.Label(j, bg="#0b0b0b")
        prev.pack(side="left", padx=16, pady=16)
        lado = tk.Frame(j, bg=BG)
        lado.pack(side="left", fill="y", padx=(0, 18), pady=16)

        def titulo_secao(t):
            tk.Label(lado, text=t, bg=BG, fg=APAGADO, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(10, 4))

        titulo_secao("FORMATO")
        botoes = {}
        for nome, desc in (("Stories", "1080 × 1920"), ("Feed", "1080 × 1350"), ("Quadrado", "1080 × 1080")):
            b = tk.Label(lado, text=f"{nome}   ·   {desc}", bg="#1e1e1e", fg=CINZA, anchor="w",
                         font=("Segoe UI Semibold", 10), padx=12, pady=7, cursor="hand2", width=22)
            b.pack(fill="x", pady=2)
            b.bind("<Button-1>", lambda e, n=nome: (estado_img.update(formato=n), desenhar()))
            botoes[nome] = b

        titulo_secao("TEXTO")
        b_dest = tk.Label(lado, bg="#1e1e1e", fg=CINZA, anchor="w", font=("Segoe UI Semibold", 10),
                          padx=12, pady=7, cursor="hand2")
        b_dest.pack(fill="x", pady=2)
        b_dest.bind("<Button-1>", lambda e: (estado_img.update(orig=not estado_img["orig"]), desenhar()))

        tk.Frame(lado, bg=BG).pack(fill="both", expand=True)
        aviso = tk.Label(lado, text="", bg=BG, fg=VERDE, font=("Segoe UI", 9), wraplength=230, justify="left")
        aviso.pack(fill="x", pady=(0, 8))

        def salvar():
            nome = re.sub(r'[\\/:*?"<>|]', "", f"{titulo} - {artista}")[:80]
            arq = os.path.join(pasta_imagens(), f"{nome} ({estado_img['formato']}) {time.strftime('%Y-%m-%d %H%M%S')}.png")
            estado_img["img"].save(arq)
            subprocess.Popen(["explorer", "/select,", arq])
            aviso.config(text=f"Salvo em Imagens\\{APP}")

        def copiar():
            aviso.config(text="Copiada! É só colar (Ctrl+V)." if copiar_imagem(estado_img["img"])
                         else "Não consegui copiar, tente de novo.")

        for texto, cmd, cor, fg in (("Salvar PNG", salvar, VERDE, "#000000"), ("Copiar imagem", copiar, "#2a2a2a", BRANCO)):
            b = tk.Label(lado, text=texto, bg=cor, fg=fg, font=("Segoe UI Semibold", 11), pady=9, cursor="hand2")
            b.pack(fill="x", pady=3)
            b.bind("<Button-1>", lambda e, c=cmd: c())

        def desenhar():
            f = estado_img["formato"]
            img = estado_img["img"] = gerar_imagem(capa, titulo, artista, pares, f, estado_img["orig"])
            alt = 640
            mini = img.resize((round(img.width * alt / img.height), alt), Image.LANCZOS)
            j.foto = ImageTk.PhotoImage(mini)
            prev.config(image=j.foto)
            for n, b in botoes.items():
                b.config(bg=VERDE if n == f else "#1e1e1e", fg="#000000" if n == f else CINZA)
            b_dest.config(text="Destaque: " + ("língua original" if estado_img["orig"] else "tradução") + "   ⇄")
            aviso.config(text="")

        desenhar()
        j.bind("<Escape>", lambda e: j.destroy())
        self.cantos_arredondados(j, escuro=True)
        j.update_idletasks()
        j.geometry(f"+{cfg['x']}+{max(0, cfg['y'] - j.winfo_height() // 2)}")
        j.focus_force()

    def atualizar_letra_completa(self, idx):
        j = self.janela_letra
        if not (j and j.winfo_exists()):
            return
        txt = j.txt
        versao = (letra["chave"], len(letra["trad"]), letra["status"])
        if versao != j.versao:
            j.versao, j.marcada = versao, None
            txt.config(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", f"{estado['titulo'] or ''}\n", "titulo")
            txt.insert("end", f"{estado['artista'] or ''}\n\n", "sub")
            j.posicoes = []
            if not letra["linhas"]:
                txt.insert("end", letra["status"], "sub")
            for i, (_, orig) in enumerate(letra["linhas"]):
                ini = txt.index("end-1c")
                trad = letra["trad"][i] if i < len(letra["trad"]) else ""
                if not orig:
                    txt.insert("end", "♪\n\n", "orig")
                else:
                    txt.insert("end", (trad or orig) + "\n", "trad")
                    if trad and normalizar(trad) != normalizar(orig):
                        txt.insert("end", orig + "\n", "orig")
                    txt.insert("end", "\n")
                j.posicoes.append((ini, txt.index("end-2c")))
            txt.config(state="disabled")
            if j.chave != letra["chave"]:  # outra música: limpa a escolha
                j.chave, j.selec = letra["chave"], set()
            self.selecionar(j.selec)
        if idx != j.marcada and 0 <= idx < len(getattr(j, "posicoes", [])):
            txt.tag_remove("atual", "1.0", "end")
            ini, fim = j.posicoes[idx]
            txt.tag_add("atual", ini, fim)
            if time.time() - j.mexeu > 8:  # não rola sozinho enquanto você escolhe linhas
                txt.see(fim)
                txt.see(ini)
            j.marcada = idx

    # ---------- loop da interface ----------
    def atualizar(self):
        try:
            self.tick()
        except Exception:
            if os.environ.get("TRADUTOR_DEBUG"):
                import traceback
                traceback.print_exc()
        self.root.after(100, self.atualizar)

    def tick(self):
        while not ui_fila.empty():
            pedido = ui_fila.get_nowait()
            if pedido == "sair":
                return self.sair()
            if pedido == "alternar":
                self.esconder() if self.root.state() == "normal" else self.mostrar()
            if isinstance(pedido, tuple) and pedido[0] == "compartilhar":
                self.abrir_compartilhar(*pedido[1])

        # mostra/esconde os controles conforme o mouse
        px, py = self.root.winfo_pointerxy()
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        dentro = x <= px < x + self.root.winfo_width() and y <= py < y + self.root.winfo_height()
        if dentro or self.menu_aberto or not cfg["auto_esconder"]:
            self.ultimo_dentro = time.time()
            self.mostrar_cabecalho(True)
        elif time.time() - self.ultimo_dentro > 1.5:
            self.mostrar_cabecalho(False)

        # cabeçalho
        tit = estado["titulo"] or APP
        self.l_tit.config(text=tit if len(tit) < 42 else tit[:40] + "…")
        pos, dur = posicao_atual(), estado["duracao"]
        art = estado["artista"] or "Toque uma música no Spotify, YouTube..."
        fonte_txt = f"  ·  {estado['fonte']}" if estado["fonte"] else ""
        self.l_art.config(text=f"{art[:30]}{fonte_txt}  ·  {mmss(pos)} / {mmss(dur)}" if estado["ativo"] else art)
        self.b_pp.config(text=G["pause"] if estado["tocando"] else G["play"])
        self.b_shuffle.set_ativo(estado["shuffle"], not estado["pode_shuffle"])
        self.b_rep.set_ativo(estado["repeat"] != 0, not estado["pode_repeat"])
        if estado["app_id"] != self.vol_app:  # trocou de player: volume passa a ser o do novo
            self.vol_app, self.vol, self.vol_tick = estado["app_id"], None, 0
        self.b_rep.config(text=G["rep1"] if estado["repeat"] == 1 else G["rep"])
        self.b_pin.config(text=G["pin"] if cfg["travado"] else G["unpin"])
        self.b_pin.set_ativo(cfg["travado"])
        off = self.offset()
        if letra.get("sinc_status") == "ouvindo" and letra.get("auto") is None and sinc_ligada() and letra["linhas"]:
            txt_sync, cor = "ouvindo…", CINZA
        else:
            txt_sync = (f"{off:+.2f}s".replace(".", ",") if off else "0,00s")
            if letra.get("auto") is not None:
                txt_sync = "auto " + txt_sync
            cor = VERDE if off or letra.get("auto") is not None else APAGADO
        self.l_sync.config(text=txt_sync, fg=cor)
        if estado["capa_v"] != self.capa_v:
            self.capa_v = estado["capa_v"]
            try:  # miniatura de vídeo é 16:9 → recorta o centro em vez de achatar
                img = ImageOps.fit(Image.open(BytesIO(estado["capa"])).convert("RGB"), (44, 44), Image.LANCZOS)
                self.img_capa = ImageTk.PhotoImage(img)
                self.capa.config(image=self.img_capa)
            except Exception:
                self.capa.config(image=self.img_padrao)

        self.atualizar_opcoes()

        # barra de progresso
        b = self.barra
        b.delete("all")
        if dur:
            b.create_rectangle(0, 0, b.winfo_width() * min(1, pos / dur), self.HB, fill=VERDE, width=0)

        # volume (a cada ~1 s)
        self.vol_tick += 1
        if self.vol_tick % 10 == 1:
            self.desenhar_volume()

        # letra
        idx = -1
        linhas = letra["linhas"]
        if not estado["ativo"]:
            self.mostrar_texto("", "Toque uma música no Spotify, YouTube ou outro player ♪", "")
        elif not linhas:
            self.mostrar_texto("", letra["status"], "")
        elif estado["sem_tempo"]:
            self.mostrar_texto("", f"O {estado['fonte'] or 'player'} não informa o tempo da música",
                               "A letra está na Letra completa (L)")
        elif self.esperando_sincronia():
            if estado["tocando"]:
                pontos = "." * (1 + int(time.time() * 2) % 3)
                self.mostrar_texto("", f"♪  Escutando a música para sincronizar a letra{pontos}",
                                   "A letra aparece assim que eu reconhecer um trecho cantado")
            else:
                self.mostrar_texto("", "Dê play para eu sincronizar a letra ♪", "")
        else:
            t = pos + self.offset()
            for k, l in enumerate(linhas):
                if l[0] <= t:
                    idx = k
                else:
                    break
            trad = letra["trad"]

            def linha(i):
                if i < 0 or i >= len(linhas):
                    return "", ""
                orig = linhas[i][1]
                tr = trad[i] if i < len(trad) else ""
                return orig, tr

            orig, tr = linha(idx)
            prox_orig, prox_tr = linha(idx + 1)
            principal = tr or orig or "♪"
            mostra_orig = cfg["original"] and tr and normalizar(tr) != normalizar(orig)
            if not trad and letra["status"]:
                mostra_orig, principal = False, orig or "♪"
            self.mostrar_texto(orig if mostra_orig else "", principal,
                               (prox_tr or prox_orig) if cfg["proxima"] else "")
        self.idx_atual = idx
        self.atualizar_letra_completa(idx)

    def mostrar_texto(self, orig, trad, prox):
        if self.l_orig.cget("text") != orig:
            self.l_orig.config(text=orig)
        if self.l_trad.cget("text") != trad:
            self.l_trad.config(text=trad)
        if self.l_prox.cget("text") != prox:
            self.l_prox.config(text=prox)


if __name__ == "__main__":
    _trava = socket.socket()
    try:
        _trava.bind(("127.0.0.1", 47811))  # já tem um aberto → não abre outro
    except OSError:
        raise SystemExit
    threading.Thread(target=loop_player, daemon=True).start()
    threading.Thread(target=loop_sincronia, daemon=True).start()
    App().root.mainloop()
