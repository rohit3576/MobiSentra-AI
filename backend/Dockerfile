# Backend image (Phase 8, Step 8.4b). Build context is the REPO ROOT
# (compose sets dockerfile: backend/Dockerfile) because src/schema reads
# the shared contract from ../../../schemas — satisfied in-image by
# copying schemas to /schemas. The root .dockerignore keeps the context
# to backend/ + schemas/ only.
FROM node:22-alpine
WORKDIR /app

RUN corepack enable
COPY backend/package.json backend/pnpm-lock.yaml backend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY backend/tsconfig.json ./
COPY backend/src ./src
COPY schemas /schemas

CMD ["pnpm", "tsx", "src/index.ts"]
