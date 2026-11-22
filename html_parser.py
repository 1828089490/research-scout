import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from pdf_parser import Segment

# 这些元素各自代表一个"章节"；段落、图、表属于离它最近的那一个
SECTION_CLASSES = {
    "ltx_section", "ltx_subsection", "ltx_subsubsection",
    "ltx_appendix", "ltx_bibliography", "ltx_abstract", "ltx_acknowledgements",
}


def clean(text: str) -> str:
    return " ".join(text.split())


def normalize_heading(text: str) -> str:
    text = clean(text)
    text = re.sub(r"^Appendix (?=[A-Z]\b)", "", text)  # "Appendix A FLARE ..." -> "A FLARE ..."
    text = re.sub(r"^([A-Z0-9]+(?:\.[0-9]+)*)\. ", r"\1 ", text)  # "2.1. Information" -> "2.1 Information"
    return text.rstrip(".")  # "Abstract." -> "Abstract"


def section_container(element: Tag) -> Tag | None:
    # 往上找最近的章节容器；段落小标题（ltx_paragraph）不算单独的章节
    for parent in element.parents:
        if isinstance(parent, Tag) and SECTION_CLASSES & set(parent.get("class", [])):
            return parent
    return None


def section_name(container: Tag) -> str:
    classes = set(container.get("class", []))
    if "ltx_bibliography" in classes:
        return "References"
    heading = container.find(re.compile(r"^h[1-6]$"), class_="ltx_title")
    return normalize_heading(heading.get_text(" ")) if heading else ""


def table_text(figure: Tag) -> str:
    # 表格逐行转成 "单元格 | 单元格 | ..."，第一行是表格标题
    lines = []
    caption = figure.find("figcaption")
    if caption:
        lines.append(clean(caption.get_text(" ")))
    for row in figure.find_all("tr"):
        cells = [clean(cell.get_text(" ")) for cell in row.find_all(["th", "td"])]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def figure_text(figure: Tag) -> str:
    # 图只保留说明文字（含子图的说明）；图片本身无法转成文字
    captions = [clean(c.get_text(" ")) for c in figure.find_all("figcaption")]
    if any(captions):
        return "\n".join(c for c in captions if c)
    # 没有说明文字的"图"，通常是用框画出来的提示词示例，里面的文字就是内容
    return clean(figure.get_text(" "))


def extract_html_segments(html_path: Path) -> list[Segment]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    # 脚注会插在句子中间，删掉；公式换成 LaTeX 原文，如 $\hat{s}_{t}$
    for note in soup.select(".ltx_note"):
        note.decompose()
    for math in soup.find_all("math"):
        math.replace_with(f" ${math.get('alttext', '')}$ ")

    segments = []
    blocks = soup.select(
        "div.ltx_para, li.ltx_bibitem, h5.ltx_title_paragraph, "
        "div.ltx_abstract > p, figure.ltx_figure, figure.ltx_table"
    )
    for block in blocks:
        if block.find_parent("figure"):
            continue  # 图里面的子图、文字，由外层的图统一处理
        container = section_container(block)
        if container is None:
            continue  # 作者、单位等不属于任何章节的内容
        section = section_name(container)
        classes = set(block.get("class", []))
        if "ltx_table" in classes:
            kind, text, anchor = "table", table_text(block), block.get("id")
        elif "ltx_figure" in classes:
            kind, text, anchor = "figure", figure_text(block), block.get("id")
        else:
            kind, text, anchor = "text", clean(block.get_text(" ")), container.get("id")
        if text:
            segments.append(Segment(section, None, text, kind, anchor))
    return segments


if __name__ == "__main__":
    for arxiv_id in ["2305.06983", "2412.15246"]:
        segments = extract_html_segments(Path(f"data/html/{arxiv_id}.html"))
        print(f"\n===== {arxiv_id}：{len(segments)} 个片段")
        for s in segments:
            if s.kind != "text":
                print(f"[{s.kind}] {s.anchor:<12} {str(s.section)[:30]:<32} {s.text[:70]}")
