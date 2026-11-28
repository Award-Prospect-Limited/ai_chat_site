FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 每次构建都装上 Debian 当时已出的安全更新（基础镜像往往滞后几天，镜像扫描会报出来）
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

COPY requirements.txt /app/requirements.txt
# pip 源：默认官方源；开单123 上构建时 release.json 的 buildArgs 传国内镜像（声明成 ARG 后 pip 会自动读这个环境变量）
ARG PIP_INDEX_URL=https://pypi.org/simple
# 基础镜像自带的 pip / setuptools 版本旧、有已修复的高危漏洞，先升到最新
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r /app/requirements.txt
# 运行时用不到 pip，装完依赖就卸掉：新版 pip 自带的清单（pip/_vendor/bom.cdx.json）里记着它内嵌的 setuptools 70.3.0，
# 镜像扫描会按 CVE-2025-47273 报高危；卸掉也少一个能在容器里装东西的工具。要在容器里临时装包时用 python -m ensurepip 找回。
RUN python -m pip uninstall -y pip

COPY ai_chat_site /app/ai_chat_site
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

RUN chmod +x /app/docker-entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

# 发布构建（release-gate 的 build.sh）传入；/healthz 据此报出提交号。放在最后，免得每次构建前面的层都失效。
ARG APP_COMMIT=""
ARG APP_BUILT_AT=""
ENV APP_COMMIT=${APP_COMMIT} \
    APP_BUILT_AT=${APP_BUILT_AT}

EXPOSE 49193

ENTRYPOINT ["/app/docker-entrypoint.sh"]

