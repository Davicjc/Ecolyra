"""Gera as imagens da plataforma para o README — só com dados de demonstração (canções de domínio
público e capas abstratas geradas), nada com direitos autorais.

    1. abra o Ecolyra (o servidor da plataforma precisa estar rodando)
    2. python docs/gerar_telas_plataforma.py

Usa um Edge headless controlado pelo DevTools (porta 9334) e grava docs/plataforma_*.png e docs/estilos.png.
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from io import BytesIO

import aiohttp
import requests
from PIL import Image

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import posts  # noqa: E402
from gerar_imagens_docs import capa_abstrata  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
W, H = 1440, 900

TWINKLE = [
    (1.0, "Twinkle, twinkle, little star", "Brilha, brilha, estrelinha"),
    (5.0, "How I wonder what you are", "Quero saber o que você é"),
    (9.0, "Up above the world so high", "Lá no alto, sobre o mundo"),
    (13.0, "Like a diamond in the sky", "Como um diamante no céu"),
    (17.0, "Twinkle, twinkle, little star", "Brilha, brilha, estrelinha"),
    (21.0, "How I wonder what you are", "Quero saber o que você é"),
    (25.0, "When the blazing sun is gone", "Quando o sol ardente se vai"),
    (29.0, "When he nothing shines upon", "Quando ele não brilha em nada"),
    (33.0, "Then you show your little light", "Você mostra sua luzinha"),
    (37.0, "Twinkle, twinkle, all the night", "Brilha, brilha, a noite inteira"),
]
DEMO = [("Twinkle, Twinkle, Little Star", "Canção tradicional", 7), ("Frère Jacques", "Canção tradicional", 3),
        ("Greensleeves", "Canção tradicional", 11), ("Scarborough Fair", "Canção tradicional", 21),
        ("Amazing Grace", "John Newton", 5), ("Ode to Joy", "Ludwig van Beethoven", 13),
        ("Ciranda, Cirandinha", "Cantiga popular", 17), ("Auld Lang Syne", "Robert Burns", 29),
        ("Danny Boy", "Canção tradicional", 31), ("Swing Low, Sweet Chariot", "Spiritual", 41)]


def data_url(img, tam=600):
    b = BytesIO()
    img.resize((tam, tam)).save(b, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def montar_js():
    faixas = [{"id": f"demo{k}", "titulo": t, "artista": a, "album": "Domínio público", "duracao": 41 + k * 17,
               "capa": data_url(capa_abstrata(600, seed)), "fonte": "Demonstração"} for k, (t, a, seed) in enumerate(DEMO)]
    faixas[0]["capa_hd"] = faixas[0]["capa"]
    letra = {"linhas": [[t, o] for t, o, _ in TWINKLE], "trad": [tr for *_, tr in TWINKLE], "sincronizada": True,
             "fonte": "demonstração", "lrc": "x"}
    estado = {"titulo": faixas[0]["titulo"], "artista": faixas[0]["artista"], "duracao": 41, "tocando": True,
              "ativo": True, "fonte": "Spotify", "app_id": "Spotify.exe", "capa_v": 999, "pos": 14.2, "offset": 0,
              "auto": None, "manual": 0, "letra_v": "demo", "status": "", "esperando": False, "interno": False,
              "modo": "flutuante", "cmds": []}
    return f"""
      window.__F = {json.dumps(faixas)};
      window.__L = {json.dumps(letra)};
      window.__E = {json.dumps(estado)};
      const __api = api;
      api = async (r, b) => {{
        if (r === 'sync') {{ __E.pos += 0.4; return JSON.parse(JSON.stringify(__E)); }}
        if (r === 'agora/letra') return {{ linhas: __L.linhas, trad: __L.trad, status: '', v: 'demo' }};
        if (r === 'historico') return __F;
        if (r.startsWith('buscar')) return {{ principal: null, resultados: __F, aviso: '' }};
        if (r === 'letra') return __L;
        return __api(r, b);
      }};
      __F.forEach(f => {{ musicas.set(f.id, f); f._letra = __L; f._letra_idioma = CONFIG.idioma || 'pt'; }});
      setInterval(() => {{ const c = document.querySelector('#pCapa'); if (c && !c.src.startsWith('data')) c.src = __F[0].capa; }}, 200);
      1"""


ROTEIRO = [
    {"js": "location.hash='#/agora'", "espera": 3, "shot": "plataforma_agora"},
    {"js": "location.hash='#/buscar'", "espera": 1},
    {"js": "document.querySelector('#q').value='canções tradicionais'; buscar('canções tradicionais')", "espera": 2,
     "shot": "plataforma_busca"},
    {"js": "location.hash='#/musica/demo0'", "espera": 2},
    {"js": "[2,3].forEach(i=>marcar(i,false)); 1", "espera": 1, "shot": "plataforma_musica"},
    {"js": "postDaMusica()", "espera": 7, "shot": "plataforma_post"},
    {"js": "location.hash='#/config'", "espera": 2, "shot": "plataforma_config"},
    {"js": "location.hash='#/sobre'", "espera": 3, "shot": "plataforma_sobre"},
]


async def rodar():
    proc = subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=9334",
                             f"--user-data-dir={os.path.join(os.environ['TEMP'], 'eco_headless')}",
                             f"--window-size={W},{H}", "--hide-scrollbars", "--mute-audio", "about:blank"])
    try:
        time.sleep(3)
        alvo = next(t for t in requests.get("http://localhost:9334/json").json() if t["type"] == "page")
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(alvo["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                n = [0]

                async def cmd(metodo, **p):
                    n[0] += 1
                    await ws.send_json({"id": n[0], "method": metodo, "params": p})
                    while True:
                        m = json.loads((await ws.receive()).data)
                        if m.get("id") == n[0]:
                            return m.get("result", m)
                await cmd("Page.enable")
                await cmd("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
                await cmd("Page.navigate", url="http://localhost:47812/#/sobre")
                await asyncio.sleep(4)
                await cmd("Runtime.evaluate", expression=montar_js(), awaitPromise=True)
                for passo in ROTEIRO:
                    await cmd("Runtime.evaluate", expression=passo["js"], awaitPromise=True)
                    await asyncio.sleep(passo.get("espera", 0))
                    if "shot" in passo:
                        r = await cmd("Page.captureScreenshot", format="png")
                        open(os.path.join(AQUI, passo["shot"] + ".png"), "wb").write(base64.b64decode(r["data"]))
                        print("ok", passo["shot"])
    finally:
        proc.kill()


def estilos():
    """Os 5 estilos de post lado a lado."""
    capa = capa_abstrata(1200, 7)
    b = BytesIO()
    capa.save(b, "PNG")
    pares = [(tr, o) for _, o, tr in TWINKLE[:3]]
    imgs = [posts.gerar_imagem(b.getvalue(), DEMO[0][0], DEMO[0][1], pares, "Stories", False, e) for e in posts.ESTILOS]
    w, h, gap = 360, 640, 24
    mosaico = Image.new("RGB", (len(imgs) * w + (len(imgs) + 1) * gap, h + 2 * gap), (13, 13, 16))
    for k, im in enumerate(imgs):
        mosaico.paste(im.resize((w, h), Image.LANCZOS), (gap + k * (w + gap), gap))
    mosaico.save(os.path.join(AQUI, "estilos.png"), optimize=True)
    print("ok estilos")


if __name__ == "__main__":
    estilos()
    asyncio.run(rodar())
