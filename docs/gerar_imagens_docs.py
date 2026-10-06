"""Gera as imagens do README (capa abstrata + exemplos), sem usar arte nem letra com direitos autorais.

    python docs/gerar_imagens_docs.py
"""
import importlib.machinery
import importlib.util
import math
import os
import random
import time
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageGrab

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
ld = importlib.machinery.SourceFileLoader("eco", os.path.join(RAIZ, "ecolyra.pyw"))
eco = importlib.util.module_from_spec(importlib.util.spec_from_loader("eco", ld))
ld.exec_module(eco)

TITULO, ARTISTA = "Twinkle, Twinkle, Little Star", "Canção tradicional"
LRC = """[00:01.00] Twinkle, twinkle, little star
[00:05.00] How I wonder what you are
[00:09.00] Up above the world so high
[00:13.00] Like a diamond in the sky"""
TRAD = ["Brilha, brilha, estrelinha", "Quero ver você brilhar", "Lá no alto, sobre o mundo", "Como um diamante no céu"]


def capa_abstrata(tam=1200, seed=7):
    """Capa de álbum fictícia: manchas de cor desfocadas + estrelinhas."""
    random.seed(seed)
    img = Image.new("RGB", (tam, tam), (14, 10, 40))
    d = ImageDraw.Draw(img)
    for cor in ((122, 61, 255), (255, 92, 168), (0, 200, 200), (255, 170, 60), (60, 90, 255)):
        x, y, r = random.randint(0, tam), random.randint(0, tam), random.randint(tam // 4, tam // 2)
        d.ellipse((x - r, y - r, x + r, y + r), fill=cor)
    img = img.filter(ImageFilter.GaussianBlur(tam // 9))
    d = ImageDraw.Draw(img)
    for _ in range(90):
        x, y, s = random.randint(0, tam), random.randint(0, tam), random.choice((1, 1, 2, 3))
        d.ellipse((x - s, y - s, x + s, y + s), fill=(255, 255, 255))
    cx, cy, R = tam * 0.68, tam * 0.3, tam * 0.07  # estrela grande
    pts = [(cx + (R if k % 2 == 0 else R * 0.42) * math.cos(math.pi / 2 + k * math.pi / 5),
            cy - (R if k % 2 == 0 else R * 0.42) * math.sin(math.pi / 2 + k * math.pi / 5)) for k in range(10)]
    d.polygon(pts, fill=(255, 240, 200))
    return img


def bytes_png(img):
    b = BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def banner(capa):
    W, H = 1280, 420
    fundo = capa.resize((W, W)).crop((0, (W - H) // 2, W, (W + H) // 2)).filter(ImageFilter.GaussianBlur(18))
    fundo = Image.blend(fundo, Image.new("RGB", (W, H), (10, 10, 18)), 0.45)
    icone = Image.open(os.path.join(RAIZ, "icone.png")).convert("RGBA").resize((170, 170), Image.LANCZOS)
    fundo.paste(icone, (110, (H - 170) // 2), icone)
    d = ImageDraw.Draw(fundo)
    f1 = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 110)
    f2 = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 38)
    d.text((320, 105), "Ecolyra", font=f1, fill=(255, 255, 255))
    d.text((326, 250), "letras traduzidas em tempo real que escutam a música", font=f2, fill=(225, 225, 235))
    fundo.save(os.path.join(AQUI, "banner.png"), optimize=True)


def exemplos(capa):
    pares = list(zip(TRAD, [l[1] for l in eco.parse_lrc(LRC)]))
    for fmt in ("Stories", "Feed"):
        img = eco.gerar_imagem(bytes_png(capa), TITULO, ARTISTA, pares, fmt)
        img.thumbnail((540, 960), Image.LANCZOS)
        img.save(os.path.join(AQUI, f"exemplo_{fmt.lower()}.png"), optimize=True)


def screenshots(capa):
    eco.letra.update(chave=(TITULO, ARTISTA), linhas=eco.parse_lrc(LRC), trad=TRAD, status="", auto=-0.4, sinc_status="")
    eco.estado.update(titulo=TITULO, artista=ARTISTA, ativo=True, duracao=60, pos=6.2, pos_ts=time.time(),
                      tocando=True, fonte="Chrome", app_id="Chrome", pode_shuffle=False, pode_repeat=False,
                      capa=bytes_png(capa.resize((300, 300))), capa_v=1)
    eco.cfg.update(auto_esconder=False, opacidade=1.0)
    eco.salvar_cfg = lambda: None  # não mexe na config de quem gera as imagens
    eco.App.iniciar_bandeja = lambda self: setattr(self, "bandeja", None)
    app = eco.App()
    app.root.geometry("+120+120")

    def foto(w, nome):
        w.lift()
        w.update()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        ImageGrab.grab((x, y, x + w.winfo_width(), y + w.winfo_height()), all_screens=True).save(
            os.path.join(AQUI, nome), optimize=True)

    def passo1():
        eco.estado.update(pos=6.2, pos_ts=time.time())
        foto(app.root, "overlay.png")
        app.abrir_letra_completa()
        app.atualizar_letra_completa(1)
        app.janela_letra.geometry("560x520+1100+120")
        app.selecionar({1, 2})
        app.root.after(800, passo2)

    def passo2():
        foto(app.janela_letra, "letra_completa.png")
        app.abrir_compartilhar(bytes_png(capa), TITULO, ARTISTA, [(TRAD[1], "How I wonder what you are"),
                                                               (TRAD[2], "Up above the world so high")])
        app.root.after(1200, passo3)

    def passo3():
        j = [w for w in app.root.winfo_children() if getattr(w, "title", lambda: "")().startswith("Imagem")][-1]
        j.geometry("+120+330")
        app.root.after(500, lambda: (foto(j, "compartilhar.png"), app.root.destroy()))

    app.root.after(1500, passo1)
    app.root.mainloop()


if __name__ == "__main__":
    capa = capa_abstrata()
    banner(capa)
    exemplos(capa)
    screenshots(capa)
    print("imagens geradas em", AQUI)
