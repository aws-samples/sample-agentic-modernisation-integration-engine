---
inclusion: manual
---
# Dev Environment Setup — Package Definitions (ADVISORY REFERENCE)

**This file is advisory. It is NOT a source of truth and it does NOT instruct you to
create anything.**

`seeds/` is the single authority for every file reproduced below. Task 1 copies those
files verbatim; it never authors them from this document. Load this only when you need
to *understand* or *modify* a seed — the reason a pin exists, what a config section is
for — and then make the change in `seeds/`, never here and never directly in the
generated tree.

If this file and `seeds/` disagree, `seeds/` is correct and this file is stale. Report
the drift rather than following it.

---

## backend/.python-version

```
3.11
```

---

## backend/pyproject.toml

Authority: `seeds/backend/pyproject.toml`. Reproduced for reference.

```toml
[project]
name = "code-transformation-engine-backend"
version = "0.1.0"
description = "Code Transformation Engine backend (FastAPI + Strands agents)."
requires-python = ">=3.11"
dependencies = [
    "fastapi==0.141.1",
    "uvicorn[standard]==0.32.1",
    "python-multipart==0.0.31",
    # boto3/botocore floor kept at >=1.35.74 for recent Bedrock runtime APIs.
    "boto3>=1.35.74,<1.36.0",
    "botocore>=1.35.74,<1.36.0",
    "strands-agents>=1.30.0,<2.0.0",
    "pydantic==2.12.5",
    "pydantic-settings==2.6.1",
    "pyjwt[crypto]==2.13.0",
    "passlib[bcrypt]==1.7.4",
    # bcrypt pinned <4.1: passlib 1.7.4's backend self-test hashes a >72-byte
    # value, which bcrypt >=4.1 rejects with ValueError.
    "bcrypt==4.0.1",
    "slowapi==0.1.9",
    "tree-sitter==0.23.2",
    "tree-sitter-java==0.23.5",
    "tree-sitter-python==0.23.6",
    "tree-sitter-javascript==0.23.1",
    # 0.21.3, NOT 0.23.x. 0.23.1 resolves and installs cleanly against
    # tree-sitter==0.23.2, then fails at first use: assigning the Language to a
    # Parser raises "ValueError: Incompatible Language version 15. Must be
    # between 13 and 14". 0.21.3 reports a supported ABI and parses C# fine.
    "tree-sitter-c-sharp==0.21.3",
    "tree-sitter-c==0.23.4",
    "GitPython==3.1.59",
    "defusedxml==0.7.1",
    "requests==2.33.0",
    "httpx==0.28.1",
    # Direct import in routes/ai_streaming.py — declared so it is not left to
    # resolve transitively via mcp at an unpinned version.
    "sse-starlette==2.1.3",
]

[dependency-groups]
dev = [
    "pytest==8.3.4",
    "hypothesis==6.122.3",
    "ruff==0.8.4",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
```

`aiohttp`, `pyyaml`, `pytest-asyncio`, `pytest-cov`, `asyncio_mode = "auto"` and a
`[tool.ruff]` config block previously appeared here and not in the seed. They are
deliberately absent: no first-party backend module imports `aiohttp` or `yaml`, no
backend test is an `async def`, and ruff's defaults are what the working tree lints
clean under. Do not reintroduce them without a caller.

---

## backend/Makefile

Authority: `seeds/backend/Makefile`. Reproduced for reference.

```makefile
.PHONY: install install-dev test lint format run clean lock export

install:
	uv sync --no-dev

install-dev:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

run:
	uv run uvicorn main:app --reload --port 8000

lock:
	uv lock

export:
	uv export --frozen --no-hashes --no-dev -o requirements.txt

clean:
	find ../temp_scripts -mindepth 1 ! -name .gitkeep ! -name .gitignore -delete 2>/dev/null || true
```

---

## frontend/package.json

```json
{
  "name": "code-insights-frontend",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "@emotion/react": "^11.11.4",
    "@emotion/styled": "^11.11.5",
    "@mui/icons-material": "^5.15.20",
    "@mui/material": "^5.15.20",
    "@mui/x-tree-view": "^7.0.0",
    "axios": "^1.7.2",
    "d3": "^7.9.0",
    "mermaid": "^11.16.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-dropzone": "^14.3.5",
    "react-markdown": "^9.0.1",
    "react-router-dom": "^6.23.1",
    "recharts": "^2.8.0",
    "remark-gfm": "^4.0.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.6",
    "@testing-library/react": "^14.3.1",
    "@types/d3": "^7.4.3",
    "@types/node": "^20.14.9",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.3.3",
    "vite": "^5.4.0",
    "vitest": "^1.6.0"
  }
}
```

---

## frontend/tsconfig.json

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["node", "vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

---

## frontend/tsconfig.node.json

Not a seed — Task 3 creates this file. Reproduced for reference only.

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

---

## frontend/vite.config.ts

```typescript
/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
});
```

---

## Bootstrap

There is no bootstrap sequence in this file. Task 1 in
`.kiro/specs/code-insights-platform/tasks.md` is the only bootstrap procedure — it copies
these files from `seeds/` and runs the lock/sync/export/install steps in the correct order.
Follow Task 1; do not author any of the files above.
