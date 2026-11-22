import os

import psycopg
from dotenv import load_dotenv

from arxiv_client import search_arxiv

UPSERT_PAPER_SQL = """
    INSERT INTO papers (
        arxiv_id, title, authors, abstract, categories,
        published_at, updated_at, doi, journal_ref, pdf_url
    )
    VALUES (
        %(arxiv_id)s, %(title)s, %(authors)s, %(abstract)s, %(categories)s,
        %(published_at)s, %(updated_at)s, %(doi)s, %(journal_ref)s, %(pdf_url)s
    )
    ON CONFLICT (arxiv_id) DO UPDATE SET
        title = EXCLUDED.title,
        authors = EXCLUDED.authors,
        abstract = EXCLUDED.abstract,
        categories = EXCLUDED.categories,
        updated_at = EXCLUDED.updated_at,
        doi = EXCLUDED.doi,
        journal_ref = EXCLUDED.journal_ref,
        pdf_url = EXCLUDED.pdf_url,
        -- arXiv 上有了新版本（更新时间变晚了），就改回 pending，触发重新解析和向量化
        ingest_status = CASE
            WHEN EXCLUDED.updated_at > papers.updated_at THEN 'pending'
            ELSE papers.ingest_status
        END
    RETURNING id
"""


def upsert_paper(conn: psycopg.Connection, paper: dict) -> int:
    row = conn.execute(UPSERT_PAPER_SQL, paper).fetchone()
    return row[0]


if __name__ == "__main__":
    load_dotenv()
    papers = search_arxiv('ti:"retrieval augmented generation"', max_results=2)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for paper in papers:
            paper_id = upsert_paper(conn, paper)
            print(paper_id, paper["arxiv_id"], paper["title"])
