import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from openai import OpenAI

from embedder import BATCH_SIZE, build_headings, embed_texts, embedding_text, section_path
from search import APPENDIX_PATTERN

QUESTIONS_FILE = Path("evals/retrieval_questions.json")
TOP_K = 5
SMALL_CHUNK_TOKENS = 100


def load_chunks(conn: psycopg.Connection) -> list[dict]:
    # 和 search_library 的默认条件一致：只看 ready 的论文、有向量的块（即非参考文献），排除附录
    rows = conn.execute(
        """
        SELECT papers.arxiv_id, papers.title, chunks.section, chunks.token_count, chunks.content
        FROM chunks
        JOIN papers ON papers.id = chunks.paper_id
        WHERE papers.ingest_status = 'ready' AND chunks.embedding IS NOT NULL
        ORDER BY papers.id, chunks.chunk_index
        """
    ).fetchall()
    chunks = [
        {"arxiv_id": a, "title": t, "section": s, "token_count": n, "content": c}
        for a, t, s, n, c in rows
    ]
    return [c for c in chunks if c["section"] is None or not re.match(APPENDIX_PATTERN, c["section"])]


# 四种"送去向量化的文字"，只有这一处不同，其他条件完全一样
def text_v0(chunk: dict, headings: dict) -> str:
    return chunk["content"]


def text_v1(chunk: dict, headings: dict) -> str:
    return embedding_text(chunk["title"], chunk["section"], chunk["content"])


def text_v2(chunk: dict, headings: dict) -> str:
    return embedding_text(chunk["title"], section_path(chunk["section"], headings[chunk["arxiv_id"]]), chunk["content"])


def text_v3(chunk: dict, headings: dict) -> str:
    if chunk["token_count"] < SMALL_CHUNK_TOKENS:
        return text_v0(chunk, headings)
    return text_v1(chunk, headings)


VARIANTS = {
    "V0 只有正文": text_v0,
    "V1 标题+章节名（旧）": text_v1,
    "V2 标题+章节路径（现用）": text_v2,
    "V3 小块不拼标题": text_v3,
}


def embed_all(client: OpenAI, texts: list[str]) -> list[list[float]]:
    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors.extend(embed_texts(client, texts[start : start + BATCH_SIZE]))
    return vectors


def dot(a: list[float], b: list[float]) -> float:
    # OpenAI 的向量长度都是 1，点积就等于余弦相似度
    return sum(x * y for x, y in zip(a, b))


def in_sections(chunk: dict, arxiv_id: str, sections: list[str]) -> bool:
    # 必须是同一篇论文：两篇论文都有 "3.2" 这一节
    if chunk["arxiv_id"] != arxiv_id:
        return False
    section = chunk["section"] or ""
    # 有编号的按"编号 + 空格"开头来匹配；没有编号的（如 Abstract）要求完全相同
    return any(section == s or section.startswith(s + " ") for s in sections)


def first_rank(question_vector: list[float], chunk_vectors: list[list[float]], chunks: list[dict], arxiv_id: str, sections: list[str]) -> int | None:
    for rank, i in enumerate(rank_chunks(question_vector, chunk_vectors), start=1):
        if in_sections(chunks[i], arxiv_id, sections):
            return rank
    return None


def count_hits(ranks: list[int | None]) -> int:
    return sum(1 for r in ranks if r is not None and r <= TOP_K)


def mrr(ranks: list[int | None]) -> float:
    # 平均倒数排名：第 1 名得 1 分，第 2 名得 1/2，第 4 名得 1/4……没找到得 0 分
    return sum(1 / r for r in ranks if r is not None) / len(ranks)


def rank_chunks(question_vector: list[float], chunk_vectors: list[list[float]]) -> list[int]:
    # 按相似度从高到低，返回块的序号
    return sorted(range(len(chunk_vectors)), key=lambda i: dot(question_vector, chunk_vectors[i]), reverse=True)


if __name__ == "__main__":
    load_dotenv()
    client = OpenAI()
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        chunks = load_chunks(conn)
    sections_by_paper = {}
    for c in chunks:
        sections_by_paper.setdefault(c["arxiv_id"], []).append(c["section"])
    headings = {arxiv_id: build_headings(sections) for arxiv_id, sections in sections_by_paper.items()}
    question_vectors = embed_all(client, [q["question"] for q in questions])
    print(f"参与评测的块：{len(chunks)}，问题：{len(questions)}")

    for name, make_text in VARIANTS.items():
        chunk_vectors = embed_all(client, [make_text(c, headings) for c in chunks])
        strict = []
        lenient = []
        for qv, q in zip(question_vectors, questions):
            gold = q["gold_sections"]
            partial = q.get("partial_sections", [])
            strict.append(first_rank(qv, chunk_vectors, chunks, q["arxiv_id"], gold))
            lenient.append(first_rank(qv, chunk_vectors, chunks, q["arxiv_id"], gold + partial))
        print(f"\n{name}：严格命中 {count_hits(strict)}/{len(questions)}，宽松命中 {count_hits(lenient)}/{len(questions)}，MRR {mrr(strict):.2f}")
        print("  完整答案的最好排名：", strict)
        print("  含部分答案的最好排名：", lenient)
        for qv, q, r in zip(question_vectors, questions, strict):
            if r is None or r > TOP_K:
                top = [f'{chunks[i]["arxiv_id"]} {chunks[i]["section"]}' for i in rank_chunks(qv, chunk_vectors)[:TOP_K]]
                print(f"  第 {q['id']} 题严格未命中，前 {TOP_K} 名：{top}")