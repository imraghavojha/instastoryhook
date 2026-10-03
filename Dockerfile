FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --uid 10001 --create-home storyhook \
    && mkdir /data && chown storyhook:storyhook /data
USER 10001:10001
VOLUME ["/data"]
ENTRYPOINT ["instastoryhook", "--data-dir", "/data"]
CMD ["run", "zero2sudo", "--quiet", "--download-media"]
