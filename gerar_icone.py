"""Gera icone.ico / icone.png: balão de fala com nota musical e "A→あ"."""
from PIL import Image, ImageDraw, ImageFont

S = 1024  # desenha grande e reduz (antialias)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# fundo: quadrado arredondado com degradê verde → azul-petróleo
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    c = (int(30 + (12 - 30) * t), int(215 + (120 - 215) * t), int(96 + (140 - 96) * t), 255)
    gd.line([(0, y), (S, y)], fill=c)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=230, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
branco = (255, 255, 255, 255)
escuro = (18, 18, 18, 255)

# balão de fala
d.rounded_rectangle([140, 170, 884, 700], radius=150, fill=branco)
d.polygon([(300, 660), (260, 860), (480, 690)], fill=branco)

# nota musical (colcheia dupla) à esquerda
d.ellipse([235, 470, 375, 580], fill=escuro)
d.ellipse([415, 430, 555, 540], fill=escuro)
d.rectangle([345, 280, 375, 530], fill=escuro)
d.rectangle([525, 240, 555, 490], fill=escuro)
d.polygon([(345, 280), (555, 240), (555, 310), (345, 350)], fill=escuro)

# "文" à direita, indicando tradução
try:
    f = ImageFont.truetype("C:/Windows/Fonts/YuGothB.ttc", 300)
except OSError:
    f = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 300)
d.text((735, 430), "文", font=f, fill=(29, 185, 84, 255), anchor="mm")

img.save("icone.png")
img.resize((256, 256), Image.LANCZOS).save(
    "icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
