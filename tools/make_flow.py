"""Generate `docs/images/flow.png`: how one message becomes one reply.

    python tools/make_flow.py

Committed as a script so the diagram can be corrected when the pipeline changes,
instead of drifting into a hand-edited PNG. Pillow is a developer dependency only.

The point of shipping a diagram rather than a screenshot is that it is *true*: a
screenshot of a chat window can be faked by anyone and says little about what the
plugin does, while this shows where a message goes and which parts are interchangeable.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "images" / "flow.png"

WIDTH = 1180
ROW = 132
BG = (26, 24, 32)
PANEL = (38, 35, 46)
LINE = (96, 86, 116)
ACCENT = (214, 170, 96)
TEXT = (232, 228, 238)
MUTED = (150, 144, 164)


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A font that can render both the Latin and the CJK labels."""
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill: int) -> None:
    draw.rounded_rectangle(box, radius=16, fill=(fill, fill + 3, fill + 8))


def arrow(draw: ImageDraw.ImageDraw, x: int, y0: int, y1: int) -> None:
    draw.line([(x, y0), (x, y1)], fill=LINE, width=3)
    draw.polygon([(x - 8, y1 - 12), (x + 8, y1 - 12), (x, y1)], fill=LINE)


def main() -> None:
    rows = 6
    height = 96 + rows * ROW
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = font(30, bold=True)
    label_font = font(21)
    small_font = font(17)
    mono_font = font(17)

    draw.text((44, 32), "一条消息是怎么变成回复的", font=title_font, fill=TEXT)
    draw.text(
        (44, 68),
        "astrbot_plugin_tavern · 世界书与预设的组装不依赖 AstrBot，可以直接复用",
        font=small_font,
        fill=MUTED,
    )

    # (label, detail, right-hand note)
    steps = [
        ("QQ / 微信 / Telegram …", "OneBot v11 等平台适配器", "AstrBot"),
        ("AstrBot 消息事件", "私聊直接触发；群聊需 @ 或唤醒词", "AstrBot"),
        (
            "tavern/main.py",
            "冷却、并发、权限、分段渲染",
            "本插件",
        ),
        (
            "tavern/core.py",
            "绑定角色卡 → 组装预设 → 世界书命中 → 裁剪历史",
            "本插件",
        ),
        (
            "tavern/st/  （不依赖 AstrBot）",
            "角色卡 V1/V2/V3 · 世界书引擎 · 预设顺序与宏 · .jsonl 分支",
            "可独立复用",
        ),
        (
            "生成后端",
            "astrbot：用 AstrBot 已配的模型      sillytavern：调用已部署的酒馆",
            "二选一",
        ),
    ]

    y = 110
    for index, (label, detail, note) in enumerate(steps):
        box = (44, y, WIDTH - 44, y + 96)
        rounded(draw, box, fill=40 if index % 2 else 34)
        draw.rounded_rectangle(box[:2] + (box[2], box[3]), radius=16, outline=(52, 48, 62), width=1)

        draw.text((70, y + 20), label, font=label_font, fill=TEXT)
        draw.text((70, y + 54), detail, font=small_font, fill=MUTED)

        note_font = mono_font if "复用" in note else small_font
        note_w = draw.textlength(note, font=note_font)
        draw.text((WIDTH - 74 - note_w, y + 34), note, font=note_font, fill=ACCENT)

        if index < len(steps) - 1:
            arrow(draw, WIDTH // 2, y + 98, y + ROW - 2)
        y += ROW

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes, {image.width}x{image.height})")


if __name__ == "__main__":
    main()
