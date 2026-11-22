import os
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv
from openai import OpenAI

from embedder import embed_texts, to_pgvector

DEFAULT_TOP_K = 5
# 附录的章节编号是单个大写字母，如 "A FLARE ..."、"B.1 ..."
# 要求字母后面紧跟空格或 ".数字"，所以不会误伤 "Abstract"、"Acknowledgements"
APPENDIX_PATTERN = r"^[A-Z](\.[0-9]+)* "


@dataclass
class SearchResult:
    arxiv_id: str
    title: str
    section: str | None
    page_start: int | None
    page_end: int | None
    chunk_index: int
    content: str
    distance: float
    kind: str
    link: str | None  # 跳到原文位置的链接；用 PDF 解析的论文没有


def search_library(
    conn: psycopg.Connection,
    client: OpenAI,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    arxiv_ids: list[str] | None = None,
    include_appendix: bool = False,
) -> list[SearchResult]:
    # 问题必须用和文本块同一个模型来向量化
    query_vector = to_pgvector(embed_texts(client, [query])[0])

    conditions = ["papers.ingest_status = 'ready'", "chunks.embedding IS NOT NULL"]
    params = {"query_vector": query_vector, "top_k": top_k}
    if not include_appendix:
        conditions.append("(chunks.section IS NULL OR chunks.section !~ %(appendix_pattern)s)")
        params["appendix_pattern"] = APPENDIX_PATTERN
    if arxiv_ids:
        conditions.append("papers.arxiv_id = ANY(%(arxiv_ids)s)")
        params["arxiv_ids"] = arxiv_ids

    # 拼进 SQL 的只有上面这些写死的条件，所有的值都通过 params 传入，不会有 SQL 注入
    sql = f"""
        SELECT papers.arxiv_id, papers.title, chunks.section,
               chunks.page_start, chunks.page_end, chunks.chunk_index, chunks.content,
               chunks.embedding <=> %(query_vector)s::vector AS distance,
               chunks.kind,
               papers.html_url || '#' || chunks.anchor AS link
        FROM chunks
        JOIN papers ON papers.id = chunks.paper_id
        WHERE {" AND ".join(conditions)}
        ORDER BY distance
        LIMIT %(top_k)s
    """
    rows = conn.execute(sql, params).fetchall()
    return [SearchResult(*row) for row in rows]


def print_results(title: str, results: list[SearchResult]) -> None:
    print(f"\n===== {title} =====")
    for r in results:
        print(f"{r.distance:.3f}  {r.arxiv_id}  {r.kind:<6} {str(r.section)[:35]:<37} {r.content[:50]}")
        print(f"       {r.link}")


if __name__ == "__main__":
    load_dotenv()
    client = OpenAI()
    question = "How does FLARE decide when to retrieve?"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        print_results("默认：排除附录", search_library(conn, client, question))
        print_results("包含附录", search_library(conn, client, question, include_appendix=True))
        print_results(
            "只搜 2412.15246",
            search_library(conn, client, question, arxiv_ids=["2412.15246"]),
        )