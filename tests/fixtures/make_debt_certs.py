"""깨끗한 그림에 스캔 효과(회색조·기울기·잡티·흐림)를 넣어 테스트용 스캔본을 만듭니다."""
import random
from pathlib import Path
from PIL import Image, ImageFilter

D = Path(__file__).parent
random.seed(7)
for src, out, angle in [("cert_table_clean.png", "cert_table_scan", 0.6), ("cert_kv_clean.png", "cert_kv_scan", -0.8)]:
    im = Image.open(D / src).convert("L")
    im = im.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC)
    px = im.load()
    for _ in range(im.width * im.height // 400):
        x, y = random.randrange(im.width), random.randrange(im.height)
        px[x, y] = random.randint(80, 200)
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    if out == "cert_table_scan":
        im.save(D / f"{out}.pdf", resolution=200)      # 이미지만 든 PDF (스캔본)
    else:
        im.save(D / f"{out}.jpg", quality=70)
    (D / src).unlink()
