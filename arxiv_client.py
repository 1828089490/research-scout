import re
import xml.etree.ElementTree as ET
from datetime import datetime
import time
from pathlib import Path
import httpx

ARXIV_API_URL = "https://export.arxiv.org/api/query"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
PDF_DIR = Path("data/pdfs")
HTML_DIR = Path("data/html")
HTML_SOURCES = [
    "https://arxiv.org/html/{arxiv_id}",  # 2023 年 12 月之后提交的论文
    "https://ar5iv.labs.arxiv.org/html/{arxiv_id}",  # 更早的论文
]
MIN_INTERVAL_SECONDS = 3.0
_last_request_at = 0.0


def _wait_for_rate_limit() -> None:
    # arXiv 要求每 3 秒最多 1 次请求：距离上次请求不足 3 秒，就先等一等
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_INTERVAL_SECONDS:
        time.sleep(MIN_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()

def parse_arxiv_id(entry_id: str) -> str:
    # "http://arxiv.org/abs/2005.11401v4"     -> "2005.11401"
    # "http://arxiv.org/abs/hep-th/9901001v1" -> "hep-th/9901001"
    path = entry_id.split("/abs/", 1)[1]
    return re.sub(r"v\d+$", "", path)


def clean_text(text: str) -> str:
    # 把换行和连续空格合并成一个空格
    return " ".join(text.split())


def parse_entry(entry: ET.Element) -> dict:
    pdf_url = None
    for link in entry.findall("atom:link", NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href")

    doi = entry.find("arxiv:doi", NS)
    journal_ref = entry.find("arxiv:journal_ref", NS)

    return {
        "arxiv_id": parse_arxiv_id(entry.findtext("atom:id", namespaces=NS)),
        "title": clean_text(entry.findtext("atom:title", namespaces=NS)),
        "authors": [a.findtext("atom:name", namespaces=NS) for a in entry.findall("atom:author", NS)],
        "abstract": clean_text(entry.findtext("atom:summary", namespaces=NS)),
        "categories": [c.get("term") for c in entry.findall("atom:category", NS)],
        "published_at": datetime.fromisoformat(entry.findtext("atom:published", namespaces=NS)),
        "updated_at": datetime.fromisoformat(entry.findtext("atom:updated", namespaces=NS)),
        "doi": doi.text if doi is not None else None,
        "journal_ref": clean_text(journal_ref.text) if journal_ref is not None else None,
        "pdf_url": pdf_url,
    }


def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    _wait_for_rate_limit()
    response = httpx.get(
        ARXIV_API_URL,
        params={"search_query": query, "max_results": max_results},
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    return [parse_entry(e) for e in root.findall("atom:entry", NS)]

def download_pdf(arxiv_id: str, pdf_url: str) -> Path:
    # 旧编号里有 "/"（如 hep-th/9901001），不能直接当文件名
    path = PDF_DIR / f"{arxiv_id.replace('/', '_')}.pdf"
    if path.exists():
        return path

    _wait_for_rate_limit()
    response = httpx.get(pdf_url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"{arxiv_id} 下载到的不是 PDF")

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return path

def download_html(arxiv_id: str) -> tuple[Path, str] | None:
    # 依次尝试两个来源，都没有就返回 None，由调用方退回到 PDF
    # 返回 (本地文件, 网页地址)；网页地址和块的 anchor 拼起来，就是跳到原文位置的链接
    name = arxiv_id.replace("/", "_")
    path = HTML_DIR / f"{name}.html"
    url_path = HTML_DIR / f"{name}.url"
    if path.exists() and url_path.exists():
        return path, url_path.read_text(encoding="utf-8")

    for template in HTML_SOURCES:
        _wait_for_rate_limit()
        response = httpx.get(template.format(arxiv_id=arxiv_id), timeout=60, follow_redirects=True)
        # LaTeXML 转换出来的页面里一定有 "ltx_document" 这个标记
        if response.status_code == 200 and b"ltx_document" in response.content:
            HTML_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            # 记下实际的网页地址（跳转后可能带上了版本号，如 .../2305.06983v2）
            url_path.write_text(str(response.url), encoding="utf-8")
            return path, str(response.url)
    return None

if __name__ == "__main__":
    papers = search_arxiv('ti:"retrieval augmented generation"', max_results=1)
    paper = papers[0]
    path = download_pdf(paper["arxiv_id"], paper["pdf_url"])
    print(path, path.stat().st_size, "bytes")