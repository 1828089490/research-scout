import os

import psycopg
from dotenv import load_dotenv

from arxiv_client import download_html, download_pdf
from chunker import Chunk, chunk_segments
from html_parser import extract_html_segments
from pdf_parser import Segment, extract_segments


def save_chunks(conn: psycopg.Connection, paper_id: int, chunks: list[Chunk]) -> None:
    # 先删掉这篇论文的旧文本块，再插入新的。事务由调用方负责
    conn.execute("DELETE FROM chunks WHERE paper_id = %s", (paper_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
                (paper_id, chunk_index, section, page_start, page_end, content, token_count, kind, anchor)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (paper_id, c.chunk_index, c.section, c.page_start, c.page_end,
                 c.content, c.token_count, c.kind, c.anchor)
                for c in chunks
            ],
        )


def parse_paper(arxiv_id: str, pdf_url: str) -> tuple[list[Segment], str | None]:
    # 优先用 HTML（结构准确、图表分开）；没有 HTML 或解析不出内容，再退回到 PDF
    html = download_html(arxiv_id)
    if html is not None:
        html_path, html_url = html
        segments = extract_html_segments(html_path)
        if segments:
            return segments, html_url
    return extract_segments(download_pdf(arxiv_id, pdf_url)), None


def ingest_paper(conn: psycopg.Connection, paper_id: int) -> int:
    arxiv_id, pdf_url = conn.execute(
        "SELECT arxiv_id, pdf_url FROM papers WHERE id = %s", (paper_id,)
    ).fetchone()
    try:
        # 慢操作（下载、解析、切块）放在事务外面
        segments, html_url = parse_paper(arxiv_id, pdf_url)
        chunks = chunk_segments(segments)
        if not chunks:
            raise ValueError("没有提取到文字")
        # "删 + 插"和记录 HTML 地址放在同一个事务里
        with conn.transaction():
            save_chunks(conn, paper_id, chunks)
            conn.execute("UPDATE papers SET html_url = %s WHERE id = %s", (html_url, paper_id))
        source = "HTML" if html_url else "PDF"
        print(f"{arxiv_id}：来源 {source}")
        return len(chunks)
    except Exception as e:
        conn.execute("UPDATE papers SET ingest_status = 'failed' WHERE id = %s", (paper_id,))
        print(f"{arxiv_id} 处理失败：{e}")
        return 0


if __name__ == "__main__":
    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        rows = conn.execute(
            "SELECT id, arxiv_id FROM papers WHERE ingest_status = 'pending' ORDER BY id"
        ).fetchall()
        for paper_id, arxiv_id in rows:
            count = ingest_paper(conn, paper_id)
            print(f"{arxiv_id}：{count} 块")
