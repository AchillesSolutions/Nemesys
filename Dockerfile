# Nemesys in one container: the web page is built, then served by the Python server alongside the API.

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install -e .
COPY --from=web /web/dist web/dist
EXPOSE 8000
CMD ["python", "-m", "kickoff", "serve", "--host", "0.0.0.0", "--port", "8000"]
