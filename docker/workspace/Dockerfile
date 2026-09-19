FROM node:22-slim

# Build tools for native modules (better-sqlite3, esbuild, unrs-resolver).
# node:22-slim is Debian-based without python3/make/g++; without these,
# any native dep without a prebuilt for the exact Node ABI falls back to
# node-gyp and fails with exit code 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 \
       make \
       g++ \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g pnpm@9.15.4

WORKDIR /app

COPY package.json pnpm-lock.yaml* ./

RUN pnpm install --no-frozen-lockfile

COPY . .

ENV HERMES_API_URL=http://hermes-agent:8642

EXPOSE 3000

CMD ["pnpm", "dev", "--host", "0.0.0.0", "--port", "3000"]
