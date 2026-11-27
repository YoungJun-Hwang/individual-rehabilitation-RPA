"""PDF·그림 파일에서 글자 줄을 뽑습니다 (3단계).

- 글자가 들어 있는 PDF(전자 발급본)는 OCR 없이 바로 읽습니다.
- 스캔본 PDF·그림(JPG/PNG/TIFF)은 Tesseract OCR(무료, 인터넷 불필요)로 읽습니다.
- OCR 전에 회색조·대비 조정 후 표의 괘선을 지웁니다. 괘선이 있으면 표 머리글과 합계 줄을 자주 놓칩니다.

결과의 각 줄에는 원문 그림에서의 위치(bbox, 그림 픽셀 단위)가 붙어 있어 대조 화면에 표시할 수 있습니다.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image, ImageFilter, ImageOps

OCR_DPI = 300
TEXT_PDF_DPI = 150


@dataclass
class Line:
    text: str
    bbox: tuple  # (x0, top, x1, bottom) 그림 픽셀
    words: list = field(default_factory=list)  # [(낱말, bbox)] 표의 열 맞추기에 씀


@dataclass
class Page:
    image: Image.Image
    lines: list[Line] = field(default_factory=list)
    ocr: bool = False


class OcrUnavailable(RuntimeError):
    pass


# ---------------- Tesseract 찾기 ----------------
def _candidates() -> list[str]:
    c = [os.environ.get("TESSERACT_CMD", ""), shutil.which("tesseract") or ""]
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            c.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    return [x for x in c if x]


def tesseract_status() -> tuple[bool, str]:
    """(사용 가능 여부, 안내 문구)"""
    try:
        import pytesseract
    except ImportError:
        return False, "pytesseract가 설치되어 있지 않습니다. 실행.bat을 다시 실행하세요."
    for cmd in _candidates():
        if Path(cmd).exists():
            pytesseract.pytesseract.tesseract_cmd = cmd
            try:
                langs = pytesseract.get_languages(config="")
            except Exception as e:
                return False, f"Tesseract를 실행할 수 없습니다: {e}"
            if "kor" not in langs:
                return False, ("Tesseract에 한국어 데이터(kor)가 없습니다. 설치 프로그램을 다시 실행해 "
                               "'Additional language data'에서 Korean을 체크하세요.")
            return True, cmd
    return False, ("Tesseract OCR이 설치되어 있지 않습니다. README의 '부채증명서 OCR' 설치 방법을 따라 설치하세요. "
                   "(글자가 들어 있는 전자 발급 PDF는 설치 없이도 읽을 수 있습니다)")


# ---------------- 전처리 ----------------
def _long_runs(dark: np.ndarray, minlen: int) -> np.ndarray:
    """각 행에서 minlen 이상 이어진 어두운 구간을 표시 (괘선 찾기)."""
    mask = np.zeros_like(dark)
    counts = dark.sum(axis=1)
    for i in np.flatnonzero(counts >= minlen):
        row = dark[i].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row, [0]))))
        for s, e in zip(edges[::2], edges[1::2]):
            if e - s >= minlen:
                mask[i, s:e] = True
    return mask


def preprocess(im: Image.Image, threshold: int = 170) -> Image.Image:
    g = ImageOps.autocontrast(ImageOps.exif_transpose(im).convert("L"), cutoff=1)
    if g.width < 1800:  # 해상도가 낮으면 키움 (OCR 정확도)
        r = 1800 / g.width
        g = g.resize((int(g.width * r), int(g.height * r)), Image.LANCZOS)
    a = np.array(g)
    dark = a < threshold
    h, w = dark.shape
    m = _long_runs(dark, max(40, w // 8)) | _long_runs(dark.T, max(40, h // 30)).T
    m[1:] |= m[:-1]
    m[:-1] |= m[1:]
    m[:, 1:] |= m[:, :-1]
    m[:, :-1] |= m[:, 1:]
    a = np.where(m, 255, a)
    out = Image.fromarray(np.where(a < threshold, 0, 255).astype(np.uint8))
    return out.filter(ImageFilter.MedianFilter(3))  # 스캔 잡티 제거


# ---------------- 읽기 ----------------
def preprocess_plain(im: Image.Image) -> Image.Image:
    """괘선을 지우지 않는 가벼운 전처리 (표가 없는 서식에서 더 잘 읽힐 때가 있음)."""
    g = ImageOps.autocontrast(ImageOps.exif_transpose(im).convert("L"), cutoff=1)
    if g.width < 1800:
        r = 1800 / g.width
        g = g.resize((int(g.width * r), int(g.height * r)), Image.LANCZOS)
    return g.filter(ImageFilter.MedianFilter(3))


MODES = {"table": (preprocess, "--psm 4 -c preserve_interword_spaces=1"),
         "plain": (preprocess_plain, "--psm 6 -c preserve_interword_spaces=1")}


def ocr_image(im: Image.Image, mode: str = "table") -> Page:
    """mode: table = 괘선 지우기 + 열 맞춤 읽기, plain = 가벼운 전처리 + 한 덩어리로 읽기"""
    ok, why = tesseract_status()
    if not ok:
        raise OcrUnavailable(why)
    import pytesseract
    src = ImageOps.exif_transpose(im).convert("RGB")
    prep, config = MODES[mode]
    pre = prep(src)
    scale = src.width / pre.width
    d = pytesseract.image_to_data(pre, lang="kor+eng", config=config, output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list[int]] = {}
    for i, word in enumerate(d["text"]):
        if word.strip() and float(d["conf"][i]) >= 0:
            groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
    lines = []
    for idx in groups.values():
        idx.sort(key=lambda i: d["left"][i])
        x0 = min(d["left"][i] for i in idx)
        top = min(d["top"][i] for i in idx)
        x1 = max(d["left"][i] + d["width"][i] for i in idx)
        bot = max(d["top"][i] + d["height"][i] for i in idx)
        words = [(d["text"][i], tuple(round(v * scale, 1) for v in (
            d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i]))) for i in idx]
        lines.append(Line(" ".join(d["text"][i] for i in idx),
                          tuple(round(v * scale, 1) for v in (x0, top, x1, bot)), words))
    lines.sort(key=lambda l: (l.bbox[1], l.bbox[0]))
    return Page(image=src, lines=lines, ocr=True)


def _char_words(chars: list[dict], s: float) -> list:
    """전자 PDF 글자들을 간격으로 끊어 낱말 목록으로 만듦."""
    words, cur = [], []
    for ch in chars:
        if ch["text"].isspace() or (cur and ch["x0"] - cur[-1]["x1"] > 1.5):
            if cur:
                words.append(cur)
            cur = [] if ch["text"].isspace() else [ch]
        else:
            cur.append(ch)
    if cur:
        words.append(cur)
    return [("".join(c["text"] for c in w),
             (w[0]["x0"] * s, min(c["top"] for c in w) * s, w[-1]["x1"] * s, max(c["bottom"] for c in w) * s))
            for w in words]


def read_file(data: bytes, filename: str, rotate: int = 0, mode: str = "table") -> list[Page]:
    """PDF 또는 그림 파일을 쪽별 글자 줄로 바꿉니다. rotate: 시계 방향 회전 각도(0/90/180/270)."""
    name = filename.lower()
    pages: list[Page] = []
    if name.endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for p in pdf.pages:
                text_lines = p.extract_text_lines() if not rotate else []
                if sum(len(l["text"].strip()) for l in text_lines) >= 20:
                    s = TEXT_PDF_DPI / 72
                    img = p.to_image(resolution=TEXT_PDF_DPI).original.convert("RGB")
                    pages.append(Page(img, [Line(l["text"], (l["x0"] * s, l["top"] * s, l["x1"] * s, l["bottom"] * s),
                                                 _char_words(l.get("chars", []), s)) for l in text_lines]))
                else:
                    img = p.to_image(resolution=OCR_DPI).original
                    pages.append(ocr_image(img.rotate(-rotate, expand=True) if rotate else img, mode))
    else:
        im = Image.open(io.BytesIO(data))
        frames = []
        try:
            while True:
                frames.append(im.copy())
                im.seek(im.tell() + 1)   # 여러 쪽 TIFF
        except EOFError:
            pass
        for f in frames:
            pages.append(ocr_image(f.rotate(-rotate, expand=True) if rotate else f, mode))
    return pages


def needs_ocr(data: bytes, filename: str) -> bool:
    if not filename.lower().endswith(".pdf"):
        return True
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return any(sum(len(l["text"].strip()) for l in p.extract_text_lines()) < 20 for p in pdf.pages)


def draw_boxes(page: Page, boxes: list[tuple[tuple, str]], max_width: int = 1100) -> Image.Image:
    """원문 대조용 그림. boxes: [(bbox, 색 이름)] 색 이름은 green/orange/gray."""
    from PIL import ImageDraw
    colors = {"green": (0, 150, 70), "orange": (230, 120, 0), "gray": (140, 140, 140), "red": (200, 0, 0)}
    im = page.image.copy()
    dr = ImageDraw.Draw(im, "RGBA")
    for bbox, color in boxes:
        c = colors.get(color, colors["gray"])
        x0, t, x1, b = bbox
        dr.rectangle((x0 - 4, t - 3, x1 + 4, b + 3), outline=c, width=3, fill=c + (35,))
    if im.width > max_width:
        r = max_width / im.width
        im = im.resize((max_width, int(im.height * r)), Image.LANCZOS)
    return im
