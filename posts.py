"""Ecolyra — posts: imagem (5 estilos × 3 formatos) e vídeo com a música de fundo.

  Estilos   vidro · cartao · capa · minimal · degrade
  Formatos  Stories 1080×1920 · Feed 1080×1350 · Quadrado 1080×1080
  Vídeo     MP4 H.264 + AAC; cada linha marcada acende na hora em que é cantada, com transição suave;
            trecho da música escolhido por você, com fade-in e fade-out no áudio (e na imagem).
"""
import colorsys
import ctypes
import functools
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from fontes import limpar_titulo, normalizar

APP = "Ecolyra"
PASTA = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(PASTA, "cache")
PNG = os.path.join(PASTA, "icone.png")
ASSINATURA = "Ecolyra  ·  by davicjc"
SEM_JANELA = 0x08000000

FORMATOS = {  # nome: (largura, altura, margem de segurança topo, base, tamanho inicial da letra)
    "Stories": (1080, 1920, 230, 300, 64),
    "Feed": (1080, 1350, 80, 80, 58),
    "Quadrado": (1080, 1080, 60, 60, 52),
}
ESTILOS = {
    "vidro": "Vidro fosco",
    "cartao": "Cartão colorido",
    "capa": "Capa em destaque",
    "minimal": "Minimal",
    "degrade": "Degradê",
}
FONTES_WIN = r"C:\Windows\Fonts"


# ---------------- texto ----------------
@functools.lru_cache(maxsize=128)
def fonte(peso, tam, escrita="latin"):
    arqs = {"latin": {"b": "segoeuib.ttf", "s": "seguisb.ttf", "r": "segoeui.ttf", "i": "georgiai.ttf"},
            "ja": {"b": "YuGothB.ttc", "s": "YuGothB.ttc", "r": "YuGothR.ttc", "i": "YuGothR.ttc"},
            "ko": {"b": "malgunbd.ttf", "s": "malgunbd.ttf", "r": "malgun.ttf", "i": "malgun.ttf"}}
    try:
        return ImageFont.truetype(os.path.join(FONTES_WIN, arqs[escrita][peso]), tam)
    except OSError:
        try:
            return ImageFont.truetype(os.path.join(FONTES_WIN, arqs["latin"][peso]), tam)
        except OSError:
            return ImageFont.load_default(tam)


def fonte_para(texto, peso, tam):
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", texto or ""):
        return fonte(peso, tam, "ja")
    if re.search(r"[\uac00-\ud7af]", texto or ""):
        return fonte(peso, tam, "ko")
    return fonte(peso, tam)


def quebrar(texto, f, largura):
    linhas, atual = [], ""
    for p in (texto or "").split():
        teste = f"{atual} {p}".strip()
        if atual and f.getlength(teste) > largura:
            linhas.append(atual)
            atual = p
        else:
            atual = teste
    return linhas + [atual] if atual else linhas


def cortar(texto, f, largura, max_linhas=2):
    ls = quebrar(texto, f, largura)
    if len(ls) > max_linhas:
        ls = ls[:max_linhas]
        while f.getlength(ls[-1] + "…") > largura and len(ls[-1]) > 1:
            ls[-1] = ls[-1][:-1]
        ls[-1] += "…"
    return ls


# ---------------- capa / cores ----------------
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
                        os.makedirs(CACHE, exist_ok=True)
                        open(arq, "wb").write(img.content)
                        return img.content
        except Exception:
            continue
    return None


def abrir_arte(capa):
    try:
        return Image.open(BytesIO(capa)).convert("RGB")
    except Exception:
        return Image.open(PNG).convert("RGB")


def cor_destaque(img):
    r, g, b = img.resize((1, 1), Image.LANCZOS).getpixel((0, 0))[:3]
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.45), max(v, 0.9))
    return int(r * 255), int(g * 255), int(b * 255)


def paleta(img, n=5):
    """Cores mais marcantes da capa (ordenadas por 'vivacidade')."""
    p = img.resize((80, 80)).quantize(n, method=Image.Quantize.MEDIANCUT).convert("RGB")
    cores = sorted(p.getcolors(6400), reverse=True)
    saida = []
    for _, c in cores:
        h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
        saida.append((s * 0.7 + v * 0.3, c))
    return [c for _, c in sorted(saida, reverse=True)] or [(40, 40, 40)]


def tom(c, s_min=0.0, s_max=1.0, v_min=0.0, v_max=1.0):
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
    r, g, b = colorsys.hsv_to_rgb(h, min(max(s, s_min), s_max), min(max(v, v_min), v_max))
    return int(r * 255), int(g * 255), int(b * 255)


def claro(c):
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) > 165


# ---------------- layout da letra ----------------
def medir(pares, largura, tam0, limite, original_destaque, sub=0.64, entre=0.85):
    """Diminui a fonte até a letra caber no espaço. → (tam, blocos, altura_total)."""
    for tam in range(tam0, 23, -2):
        tam_sub = round(tam * sub)
        blocos, total = [], 0
        for trad, orig in pares:
            grande, pequeno = (orig, trad) if original_destaque else (trad or orig, orig)
            if not pequeno or normalizar(pequeno) == normalizar(grande):
                pequeno = ""
            fg, fp = fonte_para(grande, "b", tam), fonte_para(pequeno, "r", tam_sub)
            lg = quebrar(grande, fg, largura)
            lp = quebrar(pequeno, fp, largura) if pequeno else []
            h = len(lg) * round(tam * 1.22) + (8 + len(lp) * round(tam_sub * 1.3) if lp else 0)
            blocos.append((fg, lg, fp, lp, h))
            total += h
        total += round(tam * entre) * (len(blocos) - 1)
        if total <= limite:
            break
    return tam, blocos, total


def desenhar_letra(d, blocos, tam, x, y, largura, cor=(255, 255, 255), centro=False, ativo=None, entre=0.85,
                   sub=0.64, alfa_sub=165):
    """Escreve os blocos; com 'ativo' (vídeo), a linha cantada fica acesa e as outras apagadas."""
    tam_sub = round(tam * sub)
    for i, (fg, lg, fp, lp, h) in enumerate(blocos):
        aceso = ativo is None or i == ativo
        a1, a2 = (255, alfa_sub) if aceso else (80, 60)
        for l in lg:
            dx = (largura - fg.getlength(l)) / 2 if centro else 0
            d.text((x + dx, y), l, font=fg, fill=cor + (a1,))
            y += round(tam * 1.22)
        if lp:
            y += 8
            for l in lp:
                dx = (largura - fp.getlength(l)) / 2 if centro else 0
                d.text((x + dx, y), l, font=fp, fill=cor + (a2,))
                y += round(tam_sub * 1.3)
        y += round(tam * entre)
    return y


def mini_capa(arte, tam, raio):
    mini = ImageOps.fit(arte, (tam, tam), Image.LANCZOS)
    m = Image.new("L", (tam, tam), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, tam - 1, tam - 1), raio, fill=255)
    return mini, m


def fundo_desfocado(arte, W, H, brilho=0.9, sombra=(10, 90)):
    fundo = ImageOps.fit(arte, (W // 3, H // 3), Image.LANCZOS).filter(ImageFilter.GaussianBlur(7))
    fundo = fundo.resize((W, H), Image.BICUBIC)
    fundo = ImageEnhance.Color(fundo).enhance(1.3)
    fundo = ImageEnhance.Brightness(fundo).enhance(brilho).convert("RGBA")
    grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: sombra[0] + v * (sombra[1] - sombra[0]) // 255)
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    camada.putalpha(grad)
    fundo.alpha_composite(camada)
    return fundo


def assinar(img, H, base, cor=(255, 255, 255)):
    d = ImageDraw.Draw(img)
    f = fonte("s", 24)
    w = f.getlength(ASSINATURA)
    y = H - max(46, min(base - 40, 150))
    d.text(((img.width - w) / 2, y), ASSINATURA, font=f, fill=cor + (120,))


def cabecalho(d, fundo, arte, titulo, artista, x, y, largura, tc=136, cor=(255, 255, 255)):
    """Mini capa + nome da música + artista. → altura usada."""
    f_tit, f_art = fonte_para(titulo, "b", 46), fonte_para(artista, "r", 34)
    tit_l = cortar(titulo, f_tit, largura - tc - 30)
    alt = max(tc, len(tit_l) * 56 + 46)
    mini, m = mini_capa(arte, tc, 20)
    fundo.paste(mini, (x, y + (alt - tc) // 2), m)
    tx, ty = x + tc + 30, y + (alt - (len(tit_l) * 56 + 46)) // 2
    for l in tit_l:
        d.text((tx, ty), l, font=f_tit, fill=cor + (255,))
        ty += 56
    art = cortar(artista, f_art, largura - tc - 30, 1)[0] if artista else ""
    d.text((tx, ty + 2), art, font=f_art, fill=cor + (175,))
    return alt


# ---------------- estilos ----------------
def estilo_vidro(arte, titulo, artista, pares, F, orig, ativo):
    W, H, topo, base, tam0 = F
    fundo = fundo_desfocado(arte, W, H)
    m, p = 64, 56
    cw, tl = W - 2 * m, W - 2 * m - 2 * p
    f_tit = fonte_para(titulo, "b", 46)
    alt_cab = max(136, len(cortar(titulo, f_tit, tl - 166)) * 56 + 46)
    tam, blocos, total = medir(pares, tl, tam0, H - topo - base - 2 * p - alt_cab - 56, orig)
    ch = p + alt_cab + 56 + total + p
    cx, cy = m, topo + max(0, (H - topo - base - ch) // 2)
    mascara = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((cx, cy, cx + cw, cy + ch), 44, fill=255)
    vidro = ImageEnhance.Brightness(fundo.filter(ImageFilter.GaussianBlur(28))).enhance(0.6)
    fundo.paste(vidro, (0, 0), mascara)
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    d.rounded_rectangle((cx, cy, cx + cw, cy + ch), 44, fill=(255, 255, 255, 14), outline=(255, 255, 255, 60), width=2)
    cabecalho(d, fundo, arte, titulo, artista, cx + p, cy + p, tl)
    y = cy + p + alt_cab + 26
    d.rounded_rectangle((cx + p, y, cx + p + 90, y + 5), 3, fill=cor_destaque(arte) + (255,))
    desenhar_letra(d, blocos, tam, cx + p, y + 30, tl, ativo=ativo)
    fundo.alpha_composite(camada)
    return fundo


def estilo_cartao(arte, titulo, artista, pares, F, orig, ativo):
    """Igual ao cartão de letra do Spotify: cor sólida tirada da capa, letra grande e grossa."""
    W, H, topo, base, tam0 = F
    cor = tom(paleta(arte)[0], 0.35, 0.85, 0.35, 0.62)
    texto = (0, 0, 0) if claro(cor) else (255, 255, 255)
    fundo = Image.new("RGBA", (W, H), cor + (255,))
    m = 90
    cw = W - 2 * m
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    f_tit = fonte_para(titulo, "b", 46)
    alt_cab = max(120, len(cortar(titulo, f_tit, cw - 150)) * 56 + 46)
    tam, blocos, total = medir(pares, cw, tam0 + 8, H - topo - base - alt_cab - 120, orig, sub=0.6, entre=0.7)
    bloco_h = alt_cab + 70 + total
    y0 = topo + max(0, (H - topo - base - bloco_h) // 2)
    cabecalho(d, fundo, arte, titulo, artista, m, y0, cw, tc=120, cor=texto)
    desenhar_letra(d, blocos, tam, m, y0 + alt_cab + 70, cw, cor=texto, ativo=ativo, entre=0.7, sub=0.6, alfa_sub=190)
    fundo.alpha_composite(camada)
    return fundo


def estilo_capa(arte, titulo, artista, pares, F, orig, ativo):
    """A capa grande no topo, como a tela de 'tocando agora', e a letra embaixo."""
    W, H, topo, base, tam0 = F
    fundo = fundo_desfocado(arte, W, H, brilho=0.55, sombra=(40, 160))
    m = 90
    cw = W - 2 * m
    lado = {"Stories": 640, "Feed": 420, "Quadrado": 300}.get(next(k for k, v in FORMATOS.items() if v == F), 420)
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    f_tit, f_art = fonte_para(titulo, "b", 50), fonte_para(artista, "r", 34)
    tit_l = cortar(titulo, f_tit, cw, 2)
    alt_info = len(tit_l) * 62 + 48
    espaco = H - topo - base - lado - 50 - alt_info - 60
    tam, blocos, total = medir(pares, cw, min(tam0, 56), espaco, orig)
    altura = lado + 50 + alt_info + 60 + total
    y = topo + max(0, (H - topo - base - altura) // 2)
    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle(((W - lado) // 2 + 10, y + 24, (W + lado) // 2 + 10, y + lado + 24), 36,
                                             fill=(0, 0, 0, 150))
    fundo.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(30)))
    mini, mm = mini_capa(arte, lado, 36)
    fundo.paste(mini, ((W - lado) // 2, y), mm)
    y += lado + 50
    for l in tit_l:
        d.text(((W - f_tit.getlength(l)) / 2, y), l, font=f_tit, fill=(255, 255, 255, 255))
        y += 62
    art = cortar(artista, f_art, cw, 1)[0] if artista else ""
    d.text(((W - f_art.getlength(art)) / 2, y + 4), art, font=f_art, fill=(255, 255, 255, 170))
    y += 48 + 60
    desenhar_letra(d, blocos, tam, m, y, cw, centro=True, ativo=ativo)
    fundo.alpha_composite(camada)
    return fundo


def estilo_minimal(arte, titulo, artista, pares, F, orig, ativo):
    """Fundo quase preto, aspas grandes na cor da capa, a frase em destaque e a assinatura discreta."""
    W, H, topo, base, tam0 = F
    acento = cor_destaque(arte)
    fundo = Image.new("RGBA", (W, H), (13, 13, 15, 255))
    brilho = Image.new("RGBA", (W, H), acento + (0,))
    g = Image.radial_gradient("L").resize((W * 2, H * 2)).crop((W // 2, H // 2 + H // 3, W // 2 + W, H // 2 + H // 3 + H))
    brilho.putalpha(g.point(lambda v: max(0, 70 - v * 70 // 255)))
    fundo.alpha_composite(brilho)
    m = 100
    cw = W - 2 * m
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    tam, blocos, total = medir(pares, cw, tam0 + 6, H - topo - base - 360, orig, entre=1.0)
    altura = 170 + total + 150
    y = topo + max(0, (H - topo - base - altura) // 2)
    d.text((m - 8, y - 40), "“", font=fonte("i", 240), fill=acento + (255,))
    y = desenhar_letra(d, blocos, tam, m, y + 170, cw, ativo=ativo, entre=1.0)
    y += 30
    d.rounded_rectangle((m, y, m + 60, y + 4), 2, fill=acento + (255,))
    mini, mm = mini_capa(arte, 84, 42)
    fundo.paste(mini, (m, y + 34), mm)
    f1, f2 = fonte_para(titulo, "s", 34), fonte_para(artista, "r", 28)
    d.text((m + 108, y + 36), cortar(titulo, f1, cw - 110, 1)[0], font=f1, fill=(255, 255, 255, 235))
    d.text((m + 108, y + 80), cortar(artista, f2, cw - 110, 1)[0] if artista else "", font=f2, fill=(255, 255, 255, 140))
    fundo.alpha_composite(camada)
    return fundo


def estilo_degrade(arte, titulo, artista, pares, F, orig, ativo):
    """Degradê diagonal com duas cores da capa, letra centralizada."""
    W, H, topo, base, tam0 = F
    cores = paleta(arte)
    c1 = tom(cores[0], 0.4, 0.95, 0.35, 0.8)
    c2 = tom(cores[1] if len(cores) > 1 else cores[0], 0.4, 0.95, 0.15, 0.45)
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W]
    diag = (xx * 0.55 + yy * 0.83)
    mascara = Image.fromarray((255 * diag / diag.max()).astype("uint8"), "L")
    fundo = Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mascara).convert("RGBA")
    ruido = Image.effect_noise((W // 2, H // 2), 18).resize((W, H)).convert("L")
    grao = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    grao.putalpha(ruido.point(lambda v: max(0, v - 128) // 6))
    fundo.alpha_composite(grao)
    m = 96
    cw = W - 2 * m
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    tam, blocos, total = medir(pares, cw, tam0 + 4, H - topo - base - 260, orig)
    altura = total + 220
    y = topo + max(0, (H - topo - base - altura) // 2)
    y = desenhar_letra(d, blocos, tam, m, y, cw, centro=True, ativo=ativo)
    y += 50
    f1, f2 = fonte_para(titulo, "b", 36), fonte_para(artista, "r", 30)
    t1, t2 = cortar(titulo, f1, cw - 120, 1)[0], (cortar(artista, f2, cw - 120, 1)[0] if artista else "")
    larg = 84 + 24 + max(f1.getlength(t1), f2.getlength(t2))
    x0 = (W - larg) / 2
    mini, mm = mini_capa(arte, 84, 16)
    fundo.paste(mini, (int(x0), y), mm)
    d.text((x0 + 108, y + 4), t1, font=f1, fill=(255, 255, 255, 240))
    d.text((x0 + 108, y + 48), t2, font=f2, fill=(255, 255, 255, 160))
    fundo.alpha_composite(camada)
    return fundo


DESENHAR = {"vidro": estilo_vidro, "cartao": estilo_cartao, "capa": estilo_capa,
            "minimal": estilo_minimal, "degrade": estilo_degrade}


def gerar_imagem(capa, titulo, artista, pares, formato="Stories", original_destaque=False, estilo="vidro",
                 ativo=None, assinatura=True):
    """pares = [(tradução, original), ...] → Image RGB no formato e estilo pedidos."""
    F = FORMATOS.get(formato, FORMATOS["Stories"])
    arte = abrir_arte(capa)
    img = DESENHAR.get(estilo, estilo_vidro)(arte, titulo or "", artista or "", pares, F, original_destaque, ativo)
    if assinatura:
        assinar(img, F[1], F[3], (0, 0, 0) if estilo == "cartao" and claro(img.getpixel((5, 5))[:3]) else (255, 255, 255))
    return img.convert("RGB")


# ---------------- salvar / copiar ----------------
def pasta_usuario(csidl, padrao):
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    pasta = os.path.join(buf.value or os.path.expanduser(padrao), APP)
    os.makedirs(pasta, exist_ok=True)
    return pasta


def pasta_imagens():
    return pasta_usuario(0x27, "~\\Pictures")  # Minhas imagens


def pasta_videos():
    return pasta_usuario(0x0E, "~\\Videos")  # Meus vídeos


def nome_arquivo(titulo, artista, extra, ext):
    import time
    nome = re.sub(r'[\\/:*?"<>|]', "", f"{titulo} - {artista}")[:80].strip()
    return f"{nome} ({extra}) {time.strftime('%Y-%m-%d %H%M%S')}.{ext}"


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


# ---------------- vídeo ----------------
def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg não encontrado (rode o INSTALAR.bat de novo)")


def linha_do_tempo(linhas, sel, ini, fim):
    """[(tempo_inicio_relativo, ativo)] — qual linha marcada acende em cada momento do vídeo."""
    marcas = [(None if linhas[i][0] < ini - 0.01 else linhas[i][0] - ini, k) for k, i in enumerate(sel)]
    eventos = [(0.0, None)]
    for t, k in marcas:
        if t is None or t <= 0:
            eventos = [(0.0, k)]
        elif t < fim - ini:
            eventos.append((t, k))
    return eventos


def exportar_video(capa, titulo, artista, linhas, sel, audio, ini, fim, saida, formato="Stories", estilo="vidro",
                   original_destaque=False, fade_in=1.0, fade_out=1.5, assinatura=True, fps=30, progresso=None):
    """linhas = [(tempo, original, tradução)], sel = índices marcados, [ini, fim] = trecho da música (s).
    Gera o MP4 com transição de 0,3 s entre as linhas e fade no áudio/imagem."""
    avisar = progresso or (lambda p, t="": None)
    sel = sorted(sel)
    dur = max(1.0, fim - ini)
    pares = [(linhas[i][2], linhas[i][1]) for i in sel]
    eventos = linha_do_tempo(linhas, sel, ini, fim)
    avisar(0.02, "Desenhando os quadros...")
    quadros = {}
    for _, k in eventos:
        if k not in quadros:
            quadros[k] = gerar_imagem(capa, titulo, artista, pares, formato, original_destaque, estilo,
                                      ativo=-1 if k is None else k, assinatura=assinatura)
    W, H = FORMATOS[formato][:2]
    brutos = {k: im.tobytes() for k, im in quadros.items()}
    total = int(dur * fps)
    trans = int(0.3 * fps)
    fi, fo = max(0.0, min(fade_in, dur / 2)), max(0.0, min(fade_out, dur / 2))
    filtro_a = f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS"
    if fi:
        filtro_a += f",afade=t=in:st=0:d={fi:.2f}"
    if fo:
        filtro_a += f",afade=t=out:st={dur - fo:.3f}:d={fo:.2f}"
    filtro_v = "format=yuv420p"
    if fi:
        filtro_v += f",fade=t=in:st=0:d={min(fi, 0.8):.2f}"
    if fo:
        filtro_v += f",fade=t=out:st={dur - min(fo, 1.0):.3f}:d={min(fo, 1.0):.2f}"
    tmp = saida + ".parcial.mp4"
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-ss", f"{max(0.0, ini):.3f}", "-i", audio,
           "-filter_complex", f"[0:v]{filtro_v}[v];[1:a]{filtro_a}[a]", "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
           "-t", f"{dur:.3f}", "-movflags", "+faststart", tmp]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=SEM_JANELA)
    try:
        k_atual, e_idx = eventos[0][1], 0
        anterior, mudou_em = k_atual, None
        for n in range(total):
            t = n / fps
            while e_idx + 1 < len(eventos) and eventos[e_idx + 1][0] <= t:
                e_idx += 1
                anterior, k_atual, mudou_em = k_atual, eventos[e_idx][1], n
            passo = 1.0 if mudou_em is None else (n - mudou_em) / trans
            if passo < 1 and anterior != k_atual:  # transição suave entre as linhas
                p.stdin.write(Image.blend(quadros[anterior], quadros[k_atual], passo).tobytes())
            else:
                p.stdin.write(brutos[k_atual])
            if n % 15 == 0:
                avisar(0.05 + 0.9 * n / total, "Gerando o vídeo...")
        p.stdin.close()
        erro = p.stderr.read().decode("utf-8", "replace")
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg falhou: {erro.strip()[-300:]}")
    except BrokenPipeError:
        erro = p.stderr.read().decode("utf-8", "replace")
        p.wait()
        raise RuntimeError(f"ffmpeg falhou: {erro.strip()[-300:]}")
    os.replace(tmp, saida)
    avisar(1.0, "Pronto!")
    return saida


def recortar_audio(audio, ini, fim, fade_in=1.0, fade_out=1.5):
    """Prévia do trecho (MP3) com os fades — para ouvir antes de exportar."""
    dur = max(0.5, fim - ini)
    arq = os.path.join(tempfile.gettempdir(), "ecolyra_previa_" +
                       hashlib.md5(f"{audio}|{ini:.2f}|{fim:.2f}|{fade_in}|{fade_out}".encode()).hexdigest() + ".mp3")
    if os.path.exists(arq):
        return arq
    fi, fo = max(0.0, min(fade_in, dur / 2)), max(0.0, min(fade_out, dur / 2))
    filtro = "anull"
    if fi:
        filtro = f"afade=t=in:st=0:d={fi:.2f}"
    if fo:
        filtro += f",afade=t=out:st={dur - fo:.3f}:d={fo:.2f}"
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, ini):.3f}", "-t",
                    f"{dur:.3f}", "-i", audio, "-af", filtro, "-c:a", "libmp3lame", "-b:a", "160k", arq],
                   check=True, creationflags=SEM_JANELA, timeout=120)
    return arq
