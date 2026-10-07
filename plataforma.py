"""Ecolyra — plataforma (aba única): servidor local + janela no modo app do Edge/Chrome.

A interface é a pasta web/ (HTML/CSS/JS), servida só em 127.0.0.1 com um token aleatório por sessão.
Abre numa janela própria (Edge --app), sem barra de endereço — parece um app de música.

Rotas da API (todas pedem o cabeçalho X-Token):
  GET  /api/buscar?q=           link ou nome → músicas (Deezer + iTunes + links)
  POST /api/letra               {titulo, artista, duracao, album} → letra sincronizada + tradução
  GET  /api/videos?...&tipo=    vídeos do YouTube (audio = estúdio, clipe = oficial)
  POST /api/sync                estado do player interno ↔ estado do Ecolyra (o que toca agora, letra, comandos)
  GET  /api/agora/letra         letra da música que está tocando (overlay e plataforma juntos)
  POST /api/post/imagem         prévia/salvar/copiar a imagem do post
  POST /api/post/audio          baixa o áudio para o vídeo (tarefa em segundo plano)
  POST /api/post/video          gera o MP4 (tarefa) · GET /api/tarefa?id= progresso
  GET  /api/arquivo?id=         prévia do trecho de áudio (mp3) · POST /api/abrir mostra no Explorer
  GET/POST /api/config          configurações · POST /api/atualizar verificar/aplicar update
"""
import base64
import ctypes
import json
import mimetypes
import os
import secrets
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import fontes
import posts
from versao import CRIADOR, FOOTER_JS, PORTFOLIO, REPO, VERSAO

PASTA = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(PASTA, "web")
PORTA = 47812
TOKEN = secrets.token_urlsafe(24)
ctx = {}            # funções do ecolyra.pyw (estado, comandos, tradução, config...)
tarefas = {}        # id → {progresso, texto, erro, resultado}
arquivos = {}       # id → caminho (servidos por /api/arquivo)
pagina = {"ts": 0.0, "cmds": []}  # a janela da plataforma está aberta? comandos para ela (navegar...)
HISTORICO = os.path.join(fontes.CACHE, "historico.json")


# ---------------- utilidades ----------------
def historico(adicionar=None):
    try:
        h = json.load(open(HISTORICO, encoding="utf-8"))
    except Exception:
        h = []
    if adicionar:
        k = fontes.chave_faixa(adicionar)
        h = [adicionar] + [x for x in h if fontes.chave_faixa(x) != k]
        h = h[:60]
        json.dump(h, open(HISTORICO, "w", encoding="utf-8"), ensure_ascii=False)
    return h


def letra_traduzida(meta, idioma, forcar=False):
    d = fontes.letra_para(meta.get("titulo", ""), meta.get("artista", ""), meta.get("duracao") or 0,
                          meta.get("album", ""), forcar)
    linhas = fontes.parse_lrc(d["lrc"]) if d.get("lrc") else []
    sincronizada = bool(linhas)
    if not linhas and d.get("plain"):
        linhas = [[None, l.strip()] for l in d["plain"].splitlines()]
    trad, erro = [], ""
    if linhas and idioma:
        arq = os.path.join(fontes.CACHE, "lt_" + fontes.hashlib.md5(
            f"{meta.get('titulo')}|{meta.get('artista')}|{idioma}|{len(linhas)}".encode()).hexdigest() + ".json")
        try:
            trad = json.load(open(arq, encoding="utf-8"))
        except Exception:
            try:
                trad = ctx["traduzir"]([l[1] for l in linhas], idioma)
                json.dump(trad, open(arq, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception as e:
                erro = f"Tradução indisponível agora ({e})"
    return {"linhas": linhas, "trad": trad, "sincronizada": sincronizada, "instrumental": d.get("instrumental"),
            "fonte": d.get("fonte", ""), "lrc": d.get("lrc", ""), "erro_trad": erro}


def tarefa(fn, *args):
    tid = uuid.uuid4().hex[:12]
    t = tarefas[tid] = {"progresso": 0.0, "texto": "Começando...", "erro": "", "resultado": None}

    def rodar():
        try:
            t["resultado"] = fn(lambda p, txt="": t.update(progresso=p, texto=txt or t["texto"]), *args)
            t["progresso"], t["texto"] = 1.0, t.get("texto_fim") or "Pronto!"
        except Exception as e:
            t["erro"] = str(e)
    threading.Thread(target=rodar, daemon=True).start()
    return tid


def capa_bytes(d):
    url = d.get("capa_hd") or d.get("capa") or ""
    return (posts.capa_hd(d.get("titulo", ""), d.get("artista", "")) if not url else None) or fontes.baixar_capa(url) \
        or posts.capa_hd(d.get("titulo", ""), d.get("artista", ""))


def imagem_do_post(d):
    linhas = d.get("linhas") or []  # [[t, orig, trad], ...] já marcadas
    pares = [(l[2] or "", l[1] or "") for l in linhas]
    return posts.gerar_imagem(capa_bytes(d), d.get("titulo", ""), d.get("artista", ""), pares,
                              d.get("formato", "Stories"), bool(d.get("destaque_original")),
                              d.get("estilo", "vidro"), assinatura=d.get("assinatura", True))


def gerar_video(avisar, d):
    audio = d.get("audio")
    if not audio or not os.path.exists(audio):
        raise RuntimeError("baixe o áudio primeiro")
    todas = d["todas"]  # [[t, orig, trad], ...] — a letra inteira
    linhas = [(l[0] or 0.0, l[1] or "", l[2] or "") for l in todas]
    saida = os.path.join(posts.pasta_videos(), posts.nome_arquivo(d.get("titulo", ""), d.get("artista", ""),
                                                                  d.get("formato", "Stories"), "mp4"))
    capa = capa_bytes(d)
    posts.exportar_video(capa, d.get("titulo", ""), d.get("artista", ""), linhas, d["sel"], audio,
                         float(d["ini"]), float(d["fim"]), saida, d.get("formato", "Stories"),
                         d.get("estilo", "vidro"), bool(d.get("destaque_original")), float(d.get("fade_in", 1)),
                         float(d.get("fade_out", 1.5)), d.get("assinatura", True), progresso=avisar)
    return {"arquivo": saida}


def baixar_audio(avisar, d):
    avisar(0.1, "Procurando a versão de estúdio no YouTube...")
    arq, tipo = fontes.baixar_audio(d.get("titulo", ""), d.get("artista", ""), d.get("duracao") or 0,
                                    d.get("video_audio"), d.get("preview", ""))
    return {"audio": arq, "tipo": tipo}


def previa_audio(d):
    arq = posts.recortar_audio(d["audio"], float(d["ini"]), float(d["fim"]), float(d.get("fade_in", 1)),
                               float(d.get("fade_out", 1.5)))
    fid = uuid.uuid4().hex[:12]
    arquivos[fid] = arq
    return {"id": fid}


def mostrar_no_explorer(caminho):
    if caminho and os.path.exists(caminho):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(caminho)])


# ---------------- servidor ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"Ecolyra/{VERSAO}"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _enviar(self, codigo, corpo, tipo="application/json; charset=utf-8", extra=None):
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        elif isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except (ConnectionError, OSError):
            pass

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20_000_000:
            raise ValueError("pedido grande demais")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self._rota("GET")

    def do_POST(self):
        self._rota("POST")

    def _rota(self, metodo):
        if not self._host_ok():
            return self._enviar(403, {"erro": "host"})
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._estatico(u.path)
        token = self.headers.get("X-Token") or parse_qs(u.query).get("t", [""])[0]
        if not secrets.compare_digest(token, TOKEN):
            return self._enviar(403, {"erro": "token"})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            d = self._corpo() if metodo == "POST" else {}
            r = self._api(u.path[5:], q, d)
            if r is not None:
                self._enviar(200, r)
        except Exception as e:
            self._enviar(500, {"erro": str(e)})

    def _estatico(self, caminho):
        if caminho in ("/", "/index.html"):
            html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
            cfg = {"token": TOKEN, "versao": VERSAO, "criador": CRIADOR, "portfolio": PORTFOLIO, "repo": REPO}
            html = html.replace("__ECOLYRA__", json.dumps(cfg)).replace("__FOOTER_JS__", FOOTER_JS)
            return self._enviar(200, html, "text/html; charset=utf-8")
        nome = caminho.lstrip("/")
        arq = os.path.realpath(os.path.join(WEB, nome))
        if not arq.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(arq):
            return self._enviar(404, {"erro": "não encontrado"})
        tipo = mimetypes.guess_type(arq)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo.endswith("javascript"):
            tipo += "; charset=utf-8"
        self._enviar(200, open(arq, "rb").read(), tipo)

    def _api(self, rota, q, d):
        if rota == "buscar":
            r = fontes.resolver_entrada(q.get("q", ""))
            return r
        if rota == "historico":
            return historico()
        if rota == "letra":
            if d.get("abrir"):
                historico({k: d.get(k) for k in ("id", "titulo", "artista", "album", "duracao", "capa", "capa_hd",
                                                 "preview", "fonte", "video_id")})
            return letra_traduzida(d, d.get("idioma") or ctx["config"]().get("idioma", "pt"), bool(d.get("forcar")))
        if rota == "videos":
            return fontes.escolher_videos(q.get("titulo", ""), q.get("artista", ""), float(q.get("dur") or 0),
                                          q.get("tipo", "audio"))
        if rota == "sync":
            pagina["ts"] = time.time()
            if d.get("player"):
                ctx["player_interno"](d["player"])
            cmds_pagina, pagina["cmds"] = pagina["cmds"], []
            return dict(ctx["agora"](), cmds=ctx["cmds_interno"]() + cmds_pagina)
        if rota == "agora/letra":
            return ctx["letra_agora"]()
        if rota == "agora/capa":
            b = ctx["capa_agora"]()
            if not b:
                return self._enviar(404, {"erro": "sem capa"})
            self._enviar(200, b, "image/png" if b[:4] == b"\x89PNG" else "image/jpeg")
            return None
        if rota == "comando":
            ctx["comando"](d.get("cmd"), d.get("arg"))
            return {"ok": True}
        if rota == "tocar":  # a plataforma vai tocar esta música: já deixa a letra pronta para o overlay
            ctx["preparar_interno"](d)
            return {"ok": True}
        if rota == "ajuste":
            ctx["ajustar"](d.get("delta"))
            return {"ok": True}
        if rota == "post/imagem":
            img = imagem_do_post(d)
            acao = d.get("acao", "previa")
            if acao == "salvar":
                arq = os.path.join(posts.pasta_imagens(), posts.nome_arquivo(d.get("titulo", ""), d.get("artista", ""),
                                                                            d.get("formato", "Stories"), "png"))
                img.save(arq)
                return {"arquivo": arq}
            if acao == "copiar":
                return {"ok": posts.copiar_imagem(img)}
            img.thumbnail((720, 1280))
            buf = BytesIO()
            img.save(buf, "JPEG", quality=88)
            return {"img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}
        if rota == "post/audio":
            return {"tarefa": tarefa(baixar_audio, d)}
        if rota == "post/previa":
            return previa_audio(d)
        if rota == "post/video":
            return {"tarefa": tarefa(gerar_video, d)}
        if rota == "tarefa":
            t = tarefas.get(q.get("id", ""))
            return t or {"erro": "tarefa não existe"}
        if rota == "arquivo":
            arq = arquivos.get(q.get("id", ""))
            if not arq or not os.path.exists(arq):
                return self._enviar(404, {"erro": "não encontrado"})
            self._enviar(200, open(arq, "rb").read(), mimetypes.guess_type(arq)[0] or "audio/mpeg")
            return None
        if rota == "abrir":
            alvo = d.get("caminho", "")
            if alvo.startswith((posts.pasta_videos(), posts.pasta_imagens())):
                mostrar_no_explorer(alvo)
            return {"ok": True}
        if rota == "config":
            if d:
                for k, v in d.items():
                    ctx["set_config"](k, v)
            return dict(ctx["config"](), versao=VERSAO, atualizacao=ctx["status_update"]())
        if rota == "atualizar":
            return ctx["atualizar"](d.get("acao", "verificar"))
        if rota == "link":  # abrir links (portfólio, GitHub) no navegador padrão
            url = d.get("url", "")
            if url.startswith(("https://github.com/", "https://davicjc.", "https://footer.davicjc.com/")):
                os.startfile(url)
            return {"ok": True}
        return self._enviar(404, {"erro": f"rota desconhecida: {rota}"})


servidor = {"http": None}


def iniciar(funcoes):
    """Sobe o servidor em segundo plano (uma vez)."""
    ctx.update(funcoes)
    if servidor["http"]:
        return
    global PORTA
    for porta in range(PORTA, PORTA + 20):
        try:
            servidor["http"] = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
            PORTA = porta
            break
        except OSError:
            continue
    servidor["http"].daemon_threads = True
    threading.Thread(target=servidor["http"].serve_forever, daemon=True).start()


def url(rota=""):
    return f"http://127.0.0.1:{PORTA}/" + (f"#{rota}" if rota else "")


def pagina_aberta():
    return time.time() - pagina["ts"] < 3


def _navegador():
    pf, pf86, local = (os.environ.get("ProgramFiles", r"C:\Program Files"),
                       os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("LOCALAPPDATA", ""))
    for p in (rf"{pf86}\Microsoft\Edge\Application\msedge.exe", rf"{pf}\Microsoft\Edge\Application\msedge.exe",
              rf"{pf}\Google\Chrome\Application\chrome.exe", rf"{pf86}\Google\Chrome\Application\chrome.exe",
              rf"{local}\Google\Chrome\Application\chrome.exe",
              rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe"):
        if os.path.exists(p):
            return p
    return None


def trazer_para_frente():
    """Acha a janela da plataforma (título começa com 'Ecolyra') e traz para frente."""
    u32 = ctypes.windll.user32
    achadas = []
    cb_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, cls, 64)
        if buf.value.startswith("Ecolyra") and cls.value.startswith("Chrome_WidgetWin") and u32.IsWindowVisible(hwnd):
            achadas.append(hwnd)
        return True
    u32.EnumWindows(cb_t(cb), 0)
    for h in achadas:
        u32.ShowWindow(h, 9)  # SW_RESTORE
        u32.SetForegroundWindow(h)
    return bool(achadas)


def abrir(rota="", compacta=False):
    """Abre a plataforma numa janela própria (ou navega na que já está aberta)."""
    if pagina_aberta():
        pagina["cmds"].append({"cmd": "navegar", "arg": rota})
        if trazer_para_frente():
            return
    nav = _navegador()
    if not nav:
        os.startfile(url(rota))
        return
    perfil = os.path.join(os.environ.get("LOCALAPPDATA", PASTA), "Ecolyra", "janela")
    tam = "480,860" if compacta else "1320,860"
    subprocess.Popen([nav, f"--app={url(rota)}", f"--user-data-dir={perfil}", f"--window-size={tam}",
                      "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
                      "--disable-features=Translate,msEdgeSidebarV2"], creationflags=0x08000000)
