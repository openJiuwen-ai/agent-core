from __future__ import annotations

import subprocess
from pathlib import Path

from create_reference_redefinition_slide import C, FONT, ROOT, esc, image, rect, text


OUT_DIR = ROOT / "outputs" / "reference_ppt"
SVG_PATH = OUT_DIR / "q7_reference_old_vs_new.svg"
PNG_PATH = OUT_DIR / "q7_reference_old_vs_new.png"

W, H = 1600, 900


def frame_path(global_id: int, event_id: str, local_id: int) -> Path:
    return ROOT / "decoded_frames_renumbered" / f"{global_id:06d}_{event_id}_{local_id}.png"


def picture_card(
    parts: list[str],
    *,
    x: float,
    y: float,
    w: float,
    h: float,
    img: Path | None,
    title: str,
    frame: str,
    status: str,
    status_color: str,
    note: str = "",
) -> None:
    parts.append(rect(x, y, w, h, C["white"], C["line"], 10))
    if img is not None:
        parts.append(rect(x + 8, y + 8, w - 16, 176, C["bg"], radius=6))
        parts.append(image(img, x + 12, y + 12, w - 24, 168))
    else:
        parts.append(rect(x + 8, y + 8, w - 16, 176, "#F9FBFD", C["line"], 6))
        parts.append(text("Expedia — Traveler Information", x + 16, y + 34, 10.5, C["navy"], weight=700))
        parts.append(text("Selected trip", x + 16, y + 58, 10, C["muted"], weight=700))
        parts.append(text("Route  New York NYC → Tokyo TYO", x + 16, y + 82, 10.2, C["ink"], weight=600))
        parts.append(text("Roundtrip  Oct 20 — Oct 21", x + 16, y + 106, 9.5, C["muted"]))
        parts.append(text("Selected price  $599", x + 16, y + 130, 10.2, C["ink"], weight=600))
        parts.append(text("Passenger details …", x + 16, y + 154, 9.5, C["muted"]))
    parts.append(text(title, x + w / 2, y + 207, 12.5, C["navy"], weight=700, anchor="middle"))
    parts.append(text(frame, x + w / 2, y + 227, 9.5, C["muted"], anchor="middle"))
    if note:
        parts.append(text(note, x + w / 2, y + 247, 9, C["muted"], anchor="middle"))
    parts.append(rect(x + 14, y + h - 34, w - 28, 24, status_color + "18", radius=12))
    parts.append(text(status, x + w / 2, y + h - 17, 10.5, status_color, weight=700, anchor="middle"))


def build_svg() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        rect(0, 0, W, H, C["bg"], radius=0),
        text("Q7 Reference 对照｜旧 7 张平铺帧 → 新 6 类必要事实", 42, 55, 29, C["navy"], weight=700),
        text("Summary 题仍然需要多帧；重构的目标是删除无关帧、替换弱证据，并承认等价帧", 43, 84, 15, C["muted"]),
        rect(41, 105, 1518, 3, C["line"], radius=0),
        text("BEFORE｜原始 reference 7 张", 43, 139, 17, C["red"], weight=700),
        text("所有帧被平铺在同一个列表中，无法知道每张支持哪个事实", 270, 139, 13, C["muted"]),
    ]

    old_cards = [
        (frame_path(135, "17519521795001014080", 6), "日本餐厅搜索", "…_6", "删除｜与 Q7 无关", C["red"], "US restaurant"),
        (frame_path(143, "8279218588340882978", 7), "日本首都 Tokyo", "…_7", "保留｜必要事实", C["green"], "capital"),
        (frame_path(152, "14256183083326977918", 8), "Tokyo 雨天 12°C", "…_8", "保留｜必要事实", C["green"], "weather"),
        (None, "NYC → Tokyo 航班", "…_4 TXT", "保留｜但存在替代帧", C["blue"], "flight"),
        (frame_path(166, "17401529938837871187", 8), "Financial Times 文章", "…_8", "保留｜但存在替代帧", C["blue"], "article"),
        (frame_path(172, "10598809581032488682", 5), "Tokyo 6:14 PM", "…_5", "保留｜必要事实", C["green"], "local time"),
        (frame_path(200, "15664849218756806894", 18), "酒店列表后段", "…_18", "替换｜没有显示 €57", C["gold"], "weak hotel evidence"),
    ]
    card_w, card_h, gap, start_x, old_y = 205, 292, 12, 43, 155
    for idx, (img, title, frame, status, color, note) in enumerate(old_cards):
        picture_card(parts, x=start_x + idx * (card_w + gap), y=old_y, w=card_w, h=card_h,
                     img=img, title=title, frame=frame, status=status, status_color=color, note=note)

    parts += [
        text("AFTER｜按 6 个 required facts 组织", 43, 489, 17, C["green"], weight=700),
        text("每个事实选择一张充分帧；蓝色组内任意替代帧均可", 350, 489, 13, C["muted"]),
    ]
    new_cards = [
        (frame_path(143, "8279218588340882978", 7), "事实 1｜首都", "827…_7", "固定 1 张", C["green"], "Tokyo"),
        (frame_path(152, "14256183083326977918", 8), "事实 2｜天气", "142…_8", "固定 1 张", C["green"], "Rain · 12°C"),
        (frame_path(156, "13737182162585227244", 3), "事实 3｜航班", "137…_3 / _4", "2 张任选 1 张", C["blue"], "NYC → TYO"),
        (frame_path(166, "17401529938837871187", 8), "事实 4｜文章", "174…_7 / _8", "2 张任选 1 张", C["blue"], "Financial Times"),
        (frame_path(172, "10598809581032488682", 5), "事实 5｜时间", "105…_5", "固定 1 张", C["green"], "6:14 PM · GMT+9"),
        (frame_path(198, "15664849218756806894", 16), "事实 6｜酒店对比", "156…_16", "单帧直接比较", C["blue"], "€164 vs €57"),
    ]
    card_w2, card_h2, gap2, start_x2, new_y = 238, 294, 15, 43, 510
    for idx, (img, title, frame, status, color, note) in enumerate(new_cards):
        picture_card(parts, x=start_x2 + idx * (card_w2 + gap2), y=new_y, w=card_w2, h=card_h2,
                     img=img, title=title, frame=frame, status=status, status_color=color, note=note)

    parts += [
        rect(43, 824, 1514, 52, C["navy"], radius=10),
        text("变化", 78, 857, 12, C["gold"], weight=700),
        text("删除 175…_6", 170, 857, 13, C["white"], weight=700),
        text("｜", 310, 857, 13, "#8FA5B8"),
        text("酒店 _18 → _16", 345, 857, 13, C["white"], weight=700),
        text("｜", 500, 857, 13, "#8FA5B8"),
        text("酒店联合替代 _15 + _17", 540, 857, 13, C["white"], weight=700),
        text("｜", 765, 857, 13, "#8FA5B8"),
        text("航班 _3 / _4", 800, 857, 13, C["white"], weight=700),
        text("｜", 935, 857, 13, "#8FA5B8"),
        text("文章 _7 / _8", 970, 857, 13, C["white"], weight=700),
        text("｜", 1105, 857, 13, "#8FA5B8"),
        text("最小充分证据仍为 6 张", 1140, 857, 14, C["green_bg"], weight=700),
        "</svg>",
    ]
    return "\n".join(parts)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SVG_PATH.write_text(build_svg(), encoding="utf-8")
    subprocess.run(
        ["convert", "-background", "white", "-density", "144", str(SVG_PATH), "-resize", "1600x900!", str(PNG_PATH)],
        check=True,
    )
    print(PNG_PATH)


if __name__ == "__main__":
    main()
