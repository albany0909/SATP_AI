"""Generate distinct login/trade titlebar icons from satpBolt.png.

원본 번개 색 유지. 배지 원(파란/노란 배경) 없이 L(파랑)·M(빨강)만 표시.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def fitSquare(im, size=256):
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def withLetter(im, letter, rgb):
    """우하단에 영문만 그림 (원형 배경 없음). 흰 테두리로 소형 아이콘 가독성 확보."""
    out = im.copy()
    draw = ImageDraw.Draw(out)
    s = out.size[0]
    # 타이틀바 16px에서도 읽히게 크게
    fontSize = max(int(s * 0.42), 12)
    try:
        font = ImageFont.truetype("arialbd.ttf", fontSize)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", fontSize)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # 우하단 여백
    x = s - tw - int(s * 0.06)
    y = s - th - int(s * 0.04)
    # 외곽선(흰색) → 본문(지정색)
    outline = max(2, s // 64)
    for ox in range(-outline, outline + 1):
        for oy in range(-outline, outline + 1):
            if ox == 0 and oy == 0:
                continue
            draw.text((x + ox, y + oy), letter, fill=(255, 255, 255, 255), font=font)
    draw.text((x, y), letter, fill=rgb + (255,), font=font)
    return out


def saveIco(im, path: Path):
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [im.resize((n, n), Image.Resampling.LANCZOS) for n in sizes]
    imgs[-1].save(
        path,
        format="ICO",
        sizes=[(n, n) for n in sizes],
        append_images=imgs[:-1],
    )


def main():
    src = Image.open(ASSETS / "satpBolt.png").convert("RGBA")
    base = fitSquare(src, 256)
    # 로그인 L=파랑 / 매매 M=빨강 (원본 번개, 배경원 없음)
    login = withLetter(base, "L", (30, 136, 229))
    trade = withLetter(base, "M", (229, 57, 53))
    login.save(ASSETS / "satpLogin.png")
    trade.save(ASSETS / "satpTrade.png")
    saveIco(login, ASSETS / "satpLogin.ico")
    saveIco(trade, ASSETS / "satpTrade.ico")
    print("OK", ASSETS / "satpLogin.ico", ASSETS / "satpTrade.ico")


if __name__ == "__main__":
    main()
