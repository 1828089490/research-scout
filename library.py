import os
import re
import sys

import psycopg
from dotenv import load_dotenv
from openai import AuthenticationError, OpenAI

from arxiv_client import fetch_paper
from embedder import embed_paper, mark_ready_if_done
from ingest import ingest_paper
from papers import upsert_paper


def add_paper(conn: psycopg.Connection, client: OpenAI, arxiv_id: str) -> str:
    # 去掉版本号：2305.06983v2 和 2305.06983 是同一篇论文
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id.strip())

    # 先拿 arXiv 上的最新元数据，才能知道库里的是不是旧版本（多一次请求，约 3 秒）
    paper = fetch_paper(arxiv_id)
    if paper is None:
        return "arXiv 上找不到这篇论文"

    existing = conn.execute(
        "SELECT ingest_status, updated_at FROM papers WHERE arxiv_id = %s", (arxiv_id,)
    ).fetchone()
    if existing and existing[0] == "ready" and existing[1] >= paper["updated_at"]:
        return "已在库中，且是最新版本，跳过"
    is_update = existing is not None and existing[1] < paper["updated_at"]

    # 有新版本时，upsert 会把状态改回 pending；后面的流程和新加入一篇论文完全一样
    paper_id = upsert_paper(conn, paper)
    if ingest_paper(conn, paper_id) == 0:
        return "解析或切块失败"
    embed_paper(conn, client, paper_id)
    if mark_ready_if_done(conn, paper_id):
        action = "已更新到新版本" if is_update else "已加入"
        return f"{action}：{paper['title']}"
    return "向量化没有全部完成，再运行一次会继续"


if __name__ == "__main__":
    load_dotenv()
    client = OpenAI()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        failed = []
        for arxiv_id in sys.argv[1:]:
            # 错误边界：一篇论文出错（比如网络超时），记下来，继续处理下一篇
            try:
                print(f"{arxiv_id}：{add_paper(conn, client, arxiv_id)}")
            except AuthenticationError:
                # key 不对时，后面每一篇都会失败，直接停下
                raise
            except Exception as e:
                failed.append(arxiv_id)
                print(f"{arxiv_id}：失败（{type(e).__name__}: {e}）")
        if failed:
            print(f"有 {len(failed)} 篇失败，重新运行即可继续：{' '.join(failed)}")
