import os
import re

import psycopg
from dotenv import load_dotenv
from openai import OpenAI

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 100


def section_number(section: str | None) -> str | None:
    # "3.2.1 Confidence-based ..." -> "3.2.1"；没有数字编号的章节 -> None
    match = re.match(r"^(\d+(\.\d+)*) ", section or "")
    return match.group(1) if match else None


def build_headings(sections: list[str | None]) -> dict[str, str]:
    # 一篇论文的章节表：{"3": "3 FLARE: ...", "3.2": "3.2 Direct FLARE", ...}
    headings = {}
    for section in sections:
        number = section_number(section)
        if number:
            headings[number] = section
    return headings


def section_path(section: str | None, headings: dict[str, str]) -> str | None:
    # "3.2.1 xxx" -> "3 FLARE: ... > 3.2 Direct FLARE > 3.2.1 xxx"；找不到的上级章节就跳过
    number = section_number(section)
    if number is None:
        return section
    parts = number.split(".")
    path = []
    for i in range(1, len(parts)):
        ancestor = ".".join(parts[:i])
        if ancestor in headings:
            path.append(headings[ancestor])
    path.append(section)
    return " > ".join(path)


def embedding_text(title: str, section: str | None, content: str) -> str:
    # 向量化时拼上论文标题和章节名；content 列里仍然只存原文
    parts = [title, section, content] if section else [title, content]
    return "\n".join(parts)


def to_pgvector(embedding: list[float]) -> str:
    # pgvector 能识别 "[0.1,0.2,...]" 这种文本格式
    return "[" + ",".join(str(x) for x in embedding) + "]"


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def embed_paper(conn: psycopg.Connection, client: OpenAI, paper_id: int) -> int:
    # 先查出这篇论文所有的章节，拼出章节路径要用到上级章节的标题
    sections = conn.execute(
        "SELECT DISTINCT section FROM chunks WHERE paper_id = %s", (paper_id,)
    ).fetchall()
    headings = build_headings([row[0] for row in sections])

    # 只处理还没有向量、且不是参考文献的块，所以重复运行不会重复花钱
    rows = conn.execute(
        """
        SELECT chunks.id, papers.title, chunks.section, chunks.content
        FROM chunks
        JOIN papers ON papers.id = chunks.paper_id
        WHERE chunks.paper_id = %s
          AND chunks.embedding IS NULL
          AND chunks.section IS DISTINCT FROM 'References'
        ORDER BY chunks.chunk_index
        """,
        (paper_id,),
    ).fetchall()

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        texts = [embedding_text(title, section_path(section, headings), content) for _, title, section, content in batch]
        embeddings = embed_texts(client, texts)
        params = [(to_pgvector(emb), row[0]) for emb, row in zip(embeddings, batch)]
        # 每一批单独提交：中途失败时，已经完成的批次不会丢
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany("UPDATE chunks SET embedding = %s::vector WHERE id = %s", params)
    return len(rows)


def mark_ready_if_done(conn: psycopg.Connection, paper_id: int) -> bool:
    total, missing = conn.execute(
        """
        SELECT
            count(*),
            count(*) FILTER (WHERE embedding IS NULL AND section IS DISTINCT FROM 'References')
        FROM chunks
        WHERE paper_id = %s
        """,
        (paper_id,),
    ).fetchone()
    # 一块都没有的论文（还没切块）不能算完成
    if total > 0 and missing == 0:
        conn.execute("UPDATE papers SET ingest_status = 'ready' WHERE id = %s", (paper_id,))
        return True
    return False


if __name__ == "__main__":
    load_dotenv()
    client = OpenAI()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        rows = conn.execute(
            "SELECT id, arxiv_id FROM papers WHERE ingest_status = 'pending' ORDER BY id"
        ).fetchall()
        for paper_id, arxiv_id in rows:
            try:
                count = embed_paper(conn, client, paper_id)
                ready = mark_ready_if_done(conn, paper_id)
                print(f"{arxiv_id}：本次向量化 {count} 块，ready = {ready}")
            except Exception as e:
                # 状态保持 pending，已完成的批次留在库里，下次运行会接着做
                print(f"{arxiv_id} 向量化失败：{e}")