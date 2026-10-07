"""Ecolyra — fontes de dados: identificação de músicas, letras e vídeos.

Tudo aqui é "sem efeito colateral": não mexe na música que está tocando no overlay, então serve
tanto para o player (Spotify/YouTube...) quanto para a busca por nome/link da plataforma.

  Identificação  Deezer (catálogo, capa XL, prévia de 30 s, ISRC) + iTunes (capa 1200 px)
                 + links do Spotify / YouTube / YouTube Music / Deezer / Apple Music / SoundCloud
  Letras         LRCLIB (sincronizada e simples) → NetEase (sincronizada)
  Vídeos         busca do YouTube (sem chave de API) → clipe oficial ou versão em áudio
  Áudio          yt-dlp (para exportar vídeo de post com a música de fundo)
"""
import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

import requests

from versao import VERSAO

PASTA = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(PASTA, "cache")
UA = {"User-Agent": f"Ecolyra/{VERSAO} (https://github.com/Davicjc/ecolyra)"}
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/130.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
os.makedirs(CACHE, exist_ok=True)


def metadados_limpos(app_id):
    """Spotify e o player interno do Ecolyra mandam nome/artista certinhos (vídeo do YouTube não)."""
    low = (app_id or "spotify").lower()
    return "spotify" in low or low.startswith("ecolyra")


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
    if metadados_limpos(app_id):
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
    if metadados_limpos(app_id):
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
    # catálogo (Deezer/iTunes) acha o nome "oficial" mesmo com título de vídeo bagunçado
    for m, a in cands[:3]:
        try:
            f = casar_catalogo(m, a, 0, f"{titulo} {artista} {limpar_canal(artista)}")
        except Exception:
            f = None
        if f:
            art1 = _primeiro_artista(f["artista"])
            achado = (buscar_lrclib(f["titulo"], art1, f["duracao"]) or buscar_lrclib(f["titulo"], art1, dur, 60, True)
                      or buscar_netease(f["titulo"], art1, f["duracao"]))
            if achado:
                return achado
            break
    for m, _ in cands:  # último recurso: só o título, com a duração mais rígida
        achado = buscar_lrclib(m, "", dur, 15, True)
        if achado:
            return achado
    return None



# ---------------- Catálogo: Deezer + iTunes ----------------
def _json(url, params=None, timeout=10, headers=None):
    try:
        r = requests.get(url, params=params, timeout=timeout, headers=headers or UA)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _faixa_deezer(t):
    alb = t.get("album") or {}
    return {"id": f"dz{t['id']}", "deezer_id": t["id"], "titulo": t.get("title_short") or t.get("title") or "",
            "titulo_completo": t.get("title") or "", "artista": (t.get("artist") or {}).get("name", ""),
            "album": alb.get("title", ""), "duracao": t.get("duration") or 0,
            "capa": alb.get("cover_medium") or "", "capa_hd": alb.get("cover_xl") or alb.get("cover_big") or "",
            "preview": t.get("preview") or "", "isrc": t.get("isrc", ""),
            "explicita": bool(t.get("explicit_lyrics")), "fonte": "Deezer"}


def deezer_busca(q, limite=12):
    d = _json("https://api.deezer.com/search", {"q": q, "limit": limite})
    return [_faixa_deezer(t) for t in d.get("data", []) if t.get("id") and t.get("type", "track") == "track"]


def deezer_faixa(tid):
    t = _json(f"https://api.deezer.com/track/{tid}")
    return _faixa_deezer(t) if t.get("id") else None


def _faixa_itunes(i):
    capa = i.get("artworkUrl100", "")
    return {"id": f"it{i.get('trackId')}", "itunes_id": i.get("trackId"), "titulo": i.get("trackName", ""),
            "titulo_completo": i.get("trackName", ""), "artista": i.get("artistName", ""),
            "album": i.get("collectionName", ""), "duracao": round((i.get("trackTimeMillis") or 0) / 1000),
            "capa": capa.replace("100x100bb", "300x300bb"), "capa_hd": capa.replace("100x100bb", "1200x1200bb"),
            "preview": i.get("previewUrl", ""), "isrc": "", "explicita": i.get("trackExplicitness") == "explicit",
            "fonte": "Apple Music"}


def itunes_busca(q, limite=12, pais="BR"):
    d = _json("https://itunes.apple.com/search", {"term": q, "entity": "song", "limit": limite, "country": pais})
    return [_faixa_itunes(i) for i in d.get("results", []) if i.get("kind") == "song"]


def itunes_faixa(tid):
    d = _json("https://itunes.apple.com/lookup", {"id": tid, "country": "BR"})
    r = [i for i in d.get("results", []) if i.get("kind") == "song"]
    return _faixa_itunes(r[0]) if r else None


def _primeiro_artista(a):
    return re.split(r",|&|\bfeat\.?|\bft\.?|\bx\b|×", a or "", flags=re.I)[0].strip()


def chave_faixa(f):
    return normalizar(limpar_titulo(f["titulo"])) + "|" + normalizar(_primeiro_artista(f["artista"]))


def _paralelo(*funcs):
    saida = [None] * len(funcs)

    def rodar(k, f):
        try:
            saida[k] = f()
        except Exception:
            saida[k] = None
    ts = [threading.Thread(target=rodar, args=(k, f), daemon=True) for k, f in enumerate(funcs)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    return saida


def juntar(*listas):
    """Junta resultados de várias fontes sem repetir a mesma música (a 1ª fonte manda, as outras completam)."""
    vistos, saida = {}, []
    for lista in listas:
        for f in lista or []:
            k = chave_faixa(f)
            if k in vistos:
                a = vistos[k]
                for campo in ("capa_hd", "preview", "album", "duracao", "itunes_id", "deezer_id"):
                    if not a.get(campo) and f.get(campo):
                        a[campo] = f[campo]
                continue
            vistos[k] = f
            saida.append(f)
    return saida


def buscar_musicas(q, limite=20):
    """Busca pelo nome: Deezer + iTunes ao mesmo tempo."""
    dz, it = _paralelo(lambda: deezer_busca(q, limite), lambda: itunes_busca(q, limite))
    return juntar(dz, it)[:limite]


def casar_catalogo(titulo, artista, dur=0, texto_ref=""):
    """Acha no catálogo a faixa 'oficial' de um título/artista vindos de link ou vídeo (nome limpo,
    capa HD, duração certa). texto_ref = título do vídeo + canal, para conferir."""
    ref = normalizar(texto_ref or f"{titulo} {artista}")
    for q in dict.fromkeys(x for x in (f"{artista} {titulo}".strip(), titulo) if x):
        for f in juntar(deezer_busca(q, 10), itunes_busca(q, 10)):
            nome = normalizar(limpar_titulo(f["titulo"]))
            arts = [normalizar(x) for x in re.split(r",|&|×|\bx\b|feat\.?|ft\.?", f["artista"], flags=re.I)]
            if len(nome) < 2 or nome not in ref:
                continue
            if not any(len(x) >= 2 and x in ref for x in arts):
                continue
            if dur and f["duracao"] and abs(f["duracao"] - dur) > 90:
                continue
            return f
    return None


# ---------------- Links (Spotify, YouTube, Deezer, Apple Music, SoundCloud...) ----------------
LINK = re.compile(r"(https?://\S+|spotify:track:[A-Za-z0-9]{22})")


def _seguir(url):
    try:
        return requests.get(url, headers=NAVEGADOR, timeout=10, allow_redirects=True).url
    except Exception:
        return url


def _og(url):
    try:
        html = requests.get(url, headers=NAVEGADOR, timeout=10).text
    except Exception:
        return {}
    meta = {k: v for v, k in re.findall(r'<meta[^>]+content="([^"]*)"[^>]+property="og:(\w+)"', html)}
    meta.update(re.findall(r'<meta[^>]+property="og:(\w+)"[^>]+content="([^"]*)"', html))
    return meta


def _andar(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _andar(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _andar(v)


def id_youtube(url):
    u = urlparse(url)
    if "youtu.be" in u.netloc:
        return u.path.strip("/")[:11]
    if "youtube" in u.netloc:
        if u.path.startswith(("/shorts/", "/embed/", "/live/")):
            return u.path.split("/")[2][:11]
        return (parse_qs(u.query).get("v") or [""])[0][:11]
    return ""


def link_spotify(url):
    m = re.search(r"track[/:]([A-Za-z0-9]{22})", url)
    if not m and ("spotify.link" in url or "spotify.app.link" in url):
        m = re.search(r"track[/:]([A-Za-z0-9]{22})", _seguir(url))
    if not m:
        return None
    try:
        html = requests.get(f"https://open.spotify.com/embed/track/{m.group(1)}", headers=NAVEGADOR, timeout=10).text
        dados = json.loads(re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S).group(1))
    except Exception:
        return None
    for d in _andar(dados):
        if d.get("type") == "track" and (d.get("name") or d.get("title")) and d.get("artists"):
            capa = ""
            for img in (d.get("visualIdentity") or {}).get("image", []) or (d.get("coverArt") or {}).get("sources", []):
                capa = img.get("url", capa)
            return {"titulo": d.get("name") or d.get("title"), "artista": ", ".join(a["name"] for a in d["artists"]),
                    "duracao": round((d.get("duration") or 0) / 1000), "capa": capa, "origem": "Spotify",
                    "spotify_id": m.group(1)}
    return None


def link_youtube(url):
    vid = id_youtube(url)
    if not vid:
        return None
    o = _json("https://www.youtube.com/oembed", {"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})
    if not o.get("title"):
        return {"video_id": vid, "titulo_video": "", "canal": "", "origem": "YouTube"}
    return {"video_id": vid, "titulo_video": o["title"], "canal": o.get("author_name", ""), "origem": "YouTube",
            "capa": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}


def link_deezer(url):
    if "deezer.page.link" in url or "link.deezer.com" in url or "dzr.page.link" in url:
        url = _seguir(url)
    m = re.search(r"track/(\d+)", url)
    return deezer_faixa(m.group(1)) if m else None


def link_apple(url):
    m = re.search(r"[?&]i=(\d+)", url) or re.search(r"/song/[^/]+/(\d+)", url) or re.search(r"/song/(\d+)", url)
    return itunes_faixa(m.group(1)) if m else None


def link_soundcloud(url):
    o = _json("https://soundcloud.com/oembed", {"format": "json", "url": url})
    t = o.get("title", "")
    if " by " in t:
        musica, artista = t.rsplit(" by ", 1)
        return {"titulo": musica.strip(), "artista": artista.strip(), "origem": "SoundCloud",
                "capa": o.get("thumbnail_url", "")}
    if t:
        return {"titulo_video": t, "canal": o.get("author_name", ""), "origem": "SoundCloud",
                "capa": o.get("thumbnail_url", ""), "video_id": None}
    return None


def resolver_entrada(texto):
    """Link ou nome → {'principal': faixa|None, 'resultados': [...], 'aviso': str}."""
    texto = (texto or "").strip()
    m = LINK.search(texto)
    if not m:
        return {"principal": None, "resultados": buscar_musicas(texto), "aviso": ""}
    url = m.group(1)
    low = url.lower()
    base, aviso = None, ""
    try:
        if "spotify" in low:
            base = link_spotify(url)
        elif "youtu" in low:
            base = link_youtube(url)
        elif "deezer" in low or "dzr.page" in low:
            base = link_deezer(url)
        elif "apple.com" in low or "itunes" in low:
            base = link_apple(url)
        elif "soundcloud" in low:
            base = link_soundcloud(url)
        else:
            og = _og(url)
            if og.get("title"):
                base = {"titulo_video": og["title"], "canal": og.get("site_name", ""), "origem": og.get("site_name", "link")}
    except Exception as e:
        aviso = f"Não consegui ler o link ({e})"
    if not base:
        return {"principal": None, "resultados": [], "aviso": aviso or "Não reconheci esse link. Tente colar o nome da música."}

    if base.get("deezer_id") or base.get("itunes_id"):  # já é faixa do catálogo
        principal = base
    elif base.get("titulo_video") is not None and not base.get("titulo"):  # vídeo: título bagunçado
        tv, canal = base["titulo_video"], base.get("canal", "")
        principal = None
        for mus, art in candidatos(tv, canal, "video")[:4]:
            principal = casar_catalogo(mus, art, 0, f"{tv} {canal} {limpar_canal(canal)}")
            if principal:
                break
        if not principal:  # não está no catálogo: usa o melhor palpite do título
            mus, art = (candidatos(tv, canal, "video") or [(tv, canal)])[0]
            principal = {"id": f"yt{base.get('video_id') or normalizar(tv)[:24]}", "titulo": mus, "titulo_completo": tv, "artista": art,
                         "album": "", "duracao": 0, "capa": base.get("capa", ""), "capa_hd": base.get("capa", ""),
                         "preview": "", "fonte": "YouTube"}
        principal = dict(principal, video_id=base.get("video_id"))
    else:
        principal = casar_catalogo(base["titulo"], _primeiro_artista(base["artista"]), base.get("duracao", 0))
        if principal:
            principal = dict(principal, artista=base["artista"] if len(base["artista"]) > len(principal["artista"])
                             else principal["artista"])
        else:
            principal = {"id": "sp" + base.get("spotify_id", normalizar(base["titulo"])), "titulo": base["titulo"],
                         "titulo_completo": base["titulo"], "artista": base["artista"], "album": "",
                         "duracao": base.get("duracao", 0), "capa": base.get("capa", ""),
                         "capa_hd": base.get("capa", ""), "preview": "", "fonte": base.get("origem", "")}
    principal["origem"] = base.get("origem", principal.get("fonte", ""))
    relacionados = [f for f in buscar_musicas(f"{_primeiro_artista(principal['artista'])} {principal['titulo']}", 8)
                    if chave_faixa(f) != chave_faixa(principal)]
    return {"principal": principal, "resultados": [principal] + relacionados, "aviso": aviso}


# ---------------- Letra de uma faixa (para a plataforma) ----------------
def letra_para(titulo, artista, dur=0, album="", forcar=False):
    """{'lrc': sincronizada, 'plain': simples, 'instrumental': bool, 'fonte': str} — com cache."""
    arq = os.path.join(CACHE, "l_" + hashlib.md5(f"{titulo}|{artista}|{round(dur or 0)}".encode()).hexdigest() + ".json")
    if not forcar:
        try:
            d = json.load(open(arq, encoding="utf-8"))
            if d.get("lrc") or d.get("plain") or time.time() - d.get("quando", 0) < 86400:
                return d
        except Exception:
            pass
    lrc, plain, inst, fonte = "", "", False, ""
    art1 = _primeiro_artista(artista)
    params = {"track_name": titulo, "artist_name": art1}
    if dur:
        params["duration"] = round(dur)
    if album:
        params["album_name"] = album
    for p in (params, {k: v for k, v in params.items() if k != "album_name"}):
        j = _json("https://lrclib.net/api/get", p)
        if j.get("syncedLyrics") or j.get("plainLyrics") or j.get("instrumental"):
            lrc, plain, inst, fonte = j.get("syncedLyrics") or "", j.get("plainLyrics") or "", bool(j.get("instrumental")), "LRCLIB"
            break
    if not lrc and not inst:
        lrc = buscar_lrclib(titulo, art1, dur or 0, 8 if dur else 600) or ""
        fonte = "LRCLIB" if lrc else fonte
    if not lrc and not inst:
        lrc = buscar_netease(titulo, art1, dur or 0, 8 if dur else 600) or ""
        fonte = "NetEase" if lrc else fonte
    if not plain and not lrc and not inst:  # pelo menos a letra simples (sem tempo)
        alvo = normalizar(limpar_titulo(titulo))
        for i in lrclib_busca({"q": f"{titulo} {art1}"}):
            if i.get("plainLyrics") and parecido(alvo, normalizar(limpar_titulo(i.get("trackName", ""))), False):
                plain, fonte = i["plainLyrics"], "LRCLIB"
                break
    if lrc and not plain:
        plain = "\n".join(f for _, f in parse_lrc(lrc))
    d = {"lrc": lrc, "plain": plain, "instrumental": inst, "fonte": fonte, "quando": time.time()}
    try:
        json.dump(d, open(arq, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return d


# ---------------- YouTube: vídeos ----------------
def _segundos(txt):
    s = 0
    for p in (txt or "").split(":"):
        s = s * 60 + int(p) if p.isdigit() else s
    return s


def youtube_busca(q, n=12):
    try:
        html = requests.get("https://www.youtube.com/results", headers=NAVEGADOR, timeout=10,
                            params={"search_query": q, "sp": "EgIQAQ=="}).text  # só vídeos
        dados = json.loads(re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S).group(1))
    except Exception:
        return []
    saida = []
    for d in _andar(dados):
        v = d.get("videoRenderer")
        if not v or not v.get("videoId"):
            continue
        tit = "".join(r.get("text", "") for r in (v.get("title") or {}).get("runs", []))
        canal = "".join(r.get("text", "") for r in (v.get("ownerText") or {}).get("runs", []))
        dur = _segundos((v.get("lengthText") or {}).get("simpleText", ""))
        if dur:  # sem duração = ao vivo
            saida.append({"id": v["videoId"], "titulo": tit, "canal": canal, "duracao": dur})
        if len(saida) >= n:
            break
    return saida


RUIM = r"\b(live|ao vivo|cover|karaoke|instrumental|reaction|react|8d|slowed|sped|nightcore|remix|tutorial|lesson|aula|piano)\b"


def escolher_videos(titulo, artista, dur=0, tipo="audio"):
    """Vídeos do YouTube para tocar dentro do Ecolyra, do melhor pro pior.
    tipo='audio' → versão de estúdio (a letra bate certinho); tipo='clipe' → clipe oficial."""
    art1 = _primeiro_artista(artista)
    q = f"{art1} {titulo} " + ("audio" if tipo == "audio" else "official video")
    alvo = normalizar(limpar_titulo(titulo))
    a_norm = normalizar(art1)
    pontuados = []
    for v in youtube_busca(q):
        tn, cn = normalizar(v["titulo"]), normalizar(v["canal"])
        if alvo and alvo not in tn:
            continue
        p = 0.0
        if a_norm and (a_norm in tn or a_norm in cn):
            p += 3
        if re.search(RUIM, v["titulo"], re.I) and not re.search(RUIM, titulo, re.I):
            p -= 6
        if tipo == "audio":
            # canal "Artista - Topic" (às vezes aparece só "Artista") com o título exato = faixa de estúdio
            if v["canal"].endswith(" - Topic") or (tn == alvo and cn == a_norm):
                p += 5
            if re.search(r"\b(audio|áudio|visualizer|lyric|letra)\b", v["titulo"], re.I):
                p += 2
            if re.search(r"\b(official (music )?video|clipe|m/?v)\b", v["titulo"], re.I):
                p -= 1
            if dur:
                diff = abs(v["duracao"] - dur)
                p += 4 if diff <= 2 else 2 if diff <= 6 else -3 if diff > 20 else 0
        else:
            if re.search(r"\b(official|oficial|clipe|music video|m/?v)\b", v["titulo"], re.I):
                p += 4
            if "vevo" in cn:
                p += 2
            if re.search(r"\b(lyric|letra|audio|áudio|visualizer)\b", v["titulo"], re.I):
                p -= 3
            if dur and abs(v["duracao"] - dur) > 240:
                p -= 3
        pontuados.append((-p, len(pontuados), v))
    return [v for _, _, v in sorted(pontuados)][:6]


# ---------------- Áudio (para o vídeo do post) ----------------
PASTA_AUDIO = os.path.join(CACHE, "audio")


def baixar_audio(titulo, artista, dur=0, video_id=None, preview=""):
    """Baixa a música (versão de estúdio do YouTube) para usar de fundo no vídeo do post.
    Devolve (arquivo, 'completa'|'previa'). A prévia de 30 s do Deezer/iTunes é o plano B."""
    os.makedirs(PASTA_AUDIO, exist_ok=True)
    ids = [video_id] if video_id else [v["id"] for v in escolher_videos(titulo, artista, dur, "audio")[:3]]
    erro = None
    for vid in ids:
        for ext in ("m4a", "webm", "opus", "mp3"):
            arq = os.path.join(PASTA_AUDIO, f"{vid}.{ext}")
            if os.path.exists(arq) and os.path.getsize(arq) > 50_000:
                return arq, "completa"
        try:
            import yt_dlp
            opcoes = {"format": "bestaudio[ext=m4a]/bestaudio", "outtmpl": os.path.join(PASTA_AUDIO, "%(id)s.%(ext)s"),
                      "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opcoes) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
                arq = y.prepare_filename(info)
            if os.path.exists(arq):
                return arq, "completa"
        except Exception as e:
            erro = e
    if preview:
        arq = os.path.join(PASTA_AUDIO, "previa_" + hashlib.md5(preview.encode()).hexdigest() + ".mp3")
        if not os.path.exists(arq):
            r = requests.get(preview, timeout=20)
            r.raise_for_status()
            open(arq, "wb").write(r.content)
        return arq, "previa"
    raise RuntimeError(f"não consegui baixar o áudio ({erro})")


def baixar_capa(url):
    if not url:
        return None
    arq = os.path.join(CACHE, "capa_u" + hashlib.md5(url.encode()).hexdigest() + ".jpg")
    if os.path.exists(arq):
        return open(arq, "rb").read()
    try:
        r = requests.get(url, timeout=10, headers=NAVEGADOR)
        if r.ok and len(r.content) > 1000:
            open(arq, "wb").write(r.content)
            return r.content
    except Exception:
        pass
    return None
