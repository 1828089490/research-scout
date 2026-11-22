FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /bin/

WORKDIR /app

# 先只复制依赖清单并安装依赖：代码改了、依赖没变时，这一层直接用缓存，不用重新安装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 把虚拟环境放到 PATH 最前面：容器里直接输入 python、alembic、fastapi，用的就是虚拟环境里的版本
ENV PATH="/app/.venv/bin:$PATH"

# 构建时就下载好 tiktoken 的编码文件并存进镜像，容器运行时不再依赖外网（参照 Onyx 的做法）
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken_cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# 数据库迁移
COPY alembic.ini ./
COPY migrations ./migrations
# 应用代码：根目录下的所有模块（evals/ 不放进镜像）
COPY *.py ./

EXPOSE 8000

CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
