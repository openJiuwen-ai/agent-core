from __future__ import annotations

import base64
import html
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs" / "reference_ppt"
SVG_PATH = OUTPUT_DIR / "reference_redefinition_onepage.svg"
PNG_PATH = OUTPUT_DIR / "reference_redefinition_onepage.png"
PPTX_PATH = OUTPUT_DIR / "reference_redefinition_onepage.pptx"

W, H = 1600, 900
FONT = "Noto Sans CJK SC"

C = {
    "navy": "#17324D",
    "ink": "#243447",
    "muted": "#617285",
    "bg": "#F6F8FB",
    "white": "#FFFFFF",
    "line": "#DCE3EA",
    "red": "#E76F51",
    "red_bg": "#FFF2EE",
    "green": "#238B7B",
    "green_bg": "#ECF8F5",
    "blue": "#3977E8",
    "blue_bg": "#EEF4FF",
    "purple": "#7657D5",
    "purple_bg": "#F2EFFF",
    "gold": "#F4A261",
}


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def rect(x, y, w, h, fill, stroke="none", radius=10, stroke_width=1) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>'
    )


def text(
    value: str,
    x: float,
    y: float,
    size: float,
    color: str,
    *,
    weight: int = 400,
    anchor: str = "start",
    letter_spacing: float = 0,
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}" '
        f'letter-spacing="{letter_spacing}">{esc(value)}</text>'
    )


def multiline(lines: list[str], x: float, y: float, size: float, color: str, *, weight=400, line_gap=1.35) -> str:
    spans = []
    for idx, line in enumerate(lines):
        dy = 0 if idx == 0 else size * line_gap
        spans.append(f'<tspan x="{x}" dy="{dy}">{esc(line)}</tspan>')
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}">' + "".join(spans) + "</text>"
    )


def image(path: Path, x: float, y: float, w: float, h: float) -> str:
    mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f'<image x="{x}" y="{y}" width="{w}" height="{h}" '
        f'preserveAspectRatio="xMidYMid meet" href="data:{mime};base64,{encoded}"/>'
    )


def badge(label: str, cx: float, cy: float, color: str, *, radius=15, font_size=14) -> str:
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}"/>'
        + text(label, cx, cy + font_size * 0.36, font_size, C["white"], weight=700, anchor="middle")
    )


def pill(label: str, x: float, y: float, w: float, fill: str, color: str) -> str:
    return rect(x, y, w, 26, fill, radius=13) + text(label, x + w / 2, y + 18, 11, color, weight=700, anchor="middle")


def build_svg() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        rect(0, 0, W, H, C["bg"], radius=0),
        text('REFERENCE 重新定义：从“帧列表”到“可验证证据结构”', 42, 57, 31, C["navy"], weight=700),
        text("目标：支持最小充分证据、等价帧与自动化标注", 43, 86, 15, C["muted"]),
        rect(1390, 31, 168, 32, C["blue_bg"], radius=16),
        text("BENCHMARK DESIGN", 1474, 52, 11, C["blue"], weight=700, anchor="middle", letter_spacing=0.5),
    ]

    panel_y, panel_h = 117, 652
    lx, lw = 40, 420
    mx, mw = 475, 500
    rx, rw = 990, 570

    # BEFORE panel
    parts += [
        rect(lx, panel_y, lw, panel_h, C["white"], C["line"], 13),
        text("01  BEFORE｜Flat reference list", lx + 20, panel_y + 34, 17, C["red"], weight=700),
        text("例：Q10 新建闹钟", lx + 20, panel_y + 59, 13, C["muted"]),
    ]
    frame_paths = [
        ROOT / "decoded_frames_renumbered" / f"00012{i}_1901538300128060938_{i}.png"
        for i in range(3, 9)
    ]
    thumb_x, thumb_y, tw, th, gap = lx + 20, panel_y + 80, 55, 116, 11
    for idx, frame in enumerate(frame_paths):
        x = thumb_x + idx * (tw + gap)
        parts += [
            rect(x - 2, thumb_y - 2, tw + 4, th + 4, C["white"], C["line"], 3),
            image(frame, x, thumb_y, tw, th),
            text(f"f{idx + 3}", x + tw / 2, thumb_y + th + 15, 9, C["muted"], anchor="middle"),
        ]
    parts += [
        rect(lx + 20, panel_y + 225, lw - 40, 40, C["red_bg"], "#F6C9BD", 7),
        text("reference = [f3, f4, f5, f6, f7, f8]", lx + lw / 2, panel_y + 251, 12, C["red"], weight=700, anchor="middle"),
    ]
    old_points = [
        "过程帧、结果帧混在同一个列表",
        "等价/相邻帧无法表达“任选其一”",
        "漏一张就扣分，召回多张又被算冗余",
    ]
    for idx, label in enumerate(old_points):
        y = panel_y + 310 + idx * 70
        parts += [badge("×", lx + 36, y - 5, C["red"], radius=13, font_size=15), text(label, lx + 60, y, 14, C["ink"], weight=600 if idx == 0 else 400)]
    parts += [
        rect(lx + 20, panel_y + 527, lw - 40, 65, C["red_bg"], radius=8),
        text("评测只能看", lx + lw / 2, panel_y + 553, 12, C["muted"], anchor="middle"),
        text("exact-frame overlap", lx + lw / 2, panel_y + 577, 16, C["red"], weight=700, anchor="middle"),
    ]

    # AFTER panel
    parts += [
        rect(mx, panel_y, mw, panel_h, C["white"], C["line"], 13),
        text("02  AFTER｜Fact-centered evidence", mx + 20, panel_y + 34, 17, C["green"], weight=700),
        text("先定义必要事实，再把帧挂到事实下面", mx + 20, panel_y + 59, 13, C["muted"]),
    ]
    final_frame = ROOT / "decoded_frames_renumbered" / "000128_1901538300128060938_8.png"
    parts += [
        rect(mx + 20, panel_y + 80, 111, 234, C["white"], C["green"], 5, 2),
        image(final_frame, mx + 23, panel_y + 83, 105, 228),
        text("canonical frame · f8", mx + 75, panel_y + 332, 10, C["green"], weight=700, anchor="middle"),
    ]
    facts = ["新建 6:21 AM 闹钟", "新闹钟处于开启状态", "8:30 / 9:00 保持关闭"]
    for idx, label in enumerate(facts):
        y = panel_y + 91 + idx * 74
        parts += [
            badge("✓", mx + 158, y + 17, C["green"], radius=14, font_size=13),
            rect(mx + 181, y, 285, 38, C["green_bg"], radius=7),
            text(label, mx + 197, y + 25, 14, C["ink"], weight=600),
        ]
    parts += [
        pill("required facts", mx + 155, panel_y + 300, 100, C["green_bg"], C["green"]),
        text("→", mx + 269, panel_y + 319, 17, C["muted"], weight=700, anchor="middle"),
        pill("support sets", mx + 286, panel_y + 300, 94, C["blue_bg"], C["blue"]),
        text("→", mx + 394, panel_y + 319, 17, C["muted"], weight=700, anchor="middle"),
        pill("minimum set", mx + 411, panel_y + 300, 75, C["purple_bg"], C["purple"]),
        rect(mx + 20, panel_y + 365, mw - 40, 64, C["green_bg"], "#B8E1D8", 9),
        text("6 张过程帧  →  1 张最小充分证据", mx + mw / 2, panel_y + 405, 18, C["green"], weight=700, anchor="middle"),
        text("minimal_sufficient_set = [f8]", mx + mw / 2, panel_y + 462, 13, C["navy"], weight=700, anchor="middle"),
        rect(mx + 31, panel_y + 485, mw - 62, 43, C["bg"], radius=6),
        text("background = [f3 … f7]  ·  alternatives = 同一事实的等价帧", mx + mw / 2, panel_y + 512, 11, C["muted"], anchor="middle"),
        rect(mx + 20, panel_y + 552, mw - 40, 58, C["blue_bg"], radius=8),
        text("评测对象从“某张帧”变成", mx + mw / 2, panel_y + 577, 12, C["muted"], anchor="middle"),
        text("“事实是否被覆盖”", mx + mw / 2, panel_y + 600, 16, C["navy"], weight=700, anchor="middle"),
    ]

    # AUTOMATION panel
    parts += [
        rect(rx, panel_y, rw, panel_h, C["white"], C["line"], 13),
        text("03  AUTO-ANNOTATION｜Model-in-the-loop", rx + 20, panel_y + 34, 17, C["blue"], weight=700),
        text("模型做视觉判断 · 程序做集合计算 · 人工只做仲裁", rx + 20, panel_y + 59, 13, C["muted"]),
    ]
    steps = [
        ("1", "问题解析", "scope · cutoff · required facts", C["blue"]),
        ("2", "全量事件筛查", "高召回找出所有相关 event", C["blue"]),
        ("3", "VLM 逐帧分级", "sufficient · alternative · background", C["purple"]),
        ("4", "确定性求解", "最小覆盖 / 计数 / 聚合", C["green"]),
        ("5", "独立模型复核", "仅冲突、低置信样本进入人工", C["gold"]),
    ]
    for idx, (num, title, desc, color) in enumerate(steps):
        y = panel_y + 82 + idx * 99
        parts += [
            rect(rx + 22, y, rw - 44, 76, C["bg"], C["line"], 9),
            badge(num, rx + 53, y + 38, color, radius=16, font_size=13),
            text(title, rx + 83, y + 31, 15, C["navy"], weight=700),
            text(desc, rx + 83, y + 56, 12, C["muted"]),
        ]
        if idx < len(steps) - 1:
            parts.append(text("↓", rx + rw / 2, y + 94, 14, C["line"], weight=700, anchor="middle"))
    parts += [
        rect(rx + 22, panel_y + 585, rw - 44, 42, C["blue_bg"], radius=7),
        text("自动通过高置信样本｜人工审核 disagreement queue", rx + rw / 2, panel_y + 612, 12, C["blue"], weight=700, anchor="middle"),
    ]

    # Takeaway banner
    parts += [
        rect(40, 792, 1520, 74, C["navy"], radius=12),
        rect(60, 812, 112, 34, C["blue"], radius=17),
        text("核心变化", 116, 835, 13, C["white"], weight=700, anchor="middle"),
        text("Frame-level exact match", 355, 836, 17, "#C8D5E2", weight=700, anchor="middle"),
        text("→", 560, 838, 24, C["gold"], weight=700, anchor="middle"),
        text("Fact / Event coverage  +  Sufficiency  +  Redundancy", 1040, 836, 18, C["white"], weight=700, anchor="middle"),
        "</svg>",
    ]
    return "\n".join(parts)


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def make_pptx(image_path: Path, output_path: Path) -> None:
    files: dict[str, bytes] = {
        "[Content_Types].xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
 <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
 <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
 <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
 <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
 <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
 <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>''',
        "_rels/.rels": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
 <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>''',
        "docProps/core.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Reference Redefinition</dc:title><dc:creator>Codex</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">2026-08-04T00:00:00Z</dcterms:created></cp:coreProperties>''',
        "docProps/app.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Codex</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat><Slides>1</Slides><Notes>0</Notes><HiddenSlides>0</HiddenSlides><AppVersion>1.0</AppVersion></Properties>''',
        "ppt/presentation.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>''',
        "ppt/_rels/presentation.xml.rels": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>''',
        "ppt/slides/slide1.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr><p:pic><p:nvPicPr><p:cNvPr id="2" name="Reference Redefinition One-page"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="12192000" cy="6858000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>''',
        "ppt/slides/_rels/slide1.xml.rels": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/></Relationships>''',
        "ppt/slideLayouts/slideLayout1.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>''',
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>''',
        "ppt/slideMasters/slideMaster1.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>''',
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>''',
        "ppt/theme/theme1.xml": b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Reference Theme"><a:themeElements><a:clrScheme name="Reference"><a:dk1><a:srgbClr val="17324D"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="243447"/></a:dk2><a:lt2><a:srgbClr val="F6F8FB"/></a:lt2><a:accent1><a:srgbClr val="3977E8"/></a:accent1><a:accent2><a:srgbClr val="238B7B"/></a:accent2><a:accent3><a:srgbClr val="E76F51"/></a:accent3><a:accent4><a:srgbClr val="7657D5"/></a:accent4><a:accent5><a:srgbClr val="F4A261"/></a:accent5><a:accent6><a:srgbClr val="617285"/></a:accent6><a:hlink><a:srgbClr val="3977E8"/></a:hlink><a:folHlink><a:srgbClr val="7657D5"/></a:folHlink></a:clrScheme><a:fontScheme name="Reference"><a:majorFont><a:latin typeface="Aptos Display"/><a:ea typeface="Noto Sans CJK SC"/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/><a:ea typeface="Noto Sans CJK SC"/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="Reference"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>''',
        "ppt/media/image1.png": image_path.read_bytes(),
    }
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_text(SVG_PATH, build_svg())
    subprocess.run(
        ["convert", "-background", "white", "-density", "144", str(SVG_PATH), "-resize", "1600x900!", str(PNG_PATH)],
        check=True,
    )
    make_pptx(PNG_PATH, PPTX_PATH)
    print(PPTX_PATH)


if __name__ == "__main__":
    main()
