# LangGraph Multi-Agent System

An autonomous multi-agent orchestration framework built on LangGraph.

## Quick Start

### 1. Environment Setup

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

### 2. Machine User Configuration

To enable human PR reviews and separate the automated agent identity from developer identities:
- Create a dedicated GitHub Machine User (a secondary bot account).
- Generate a fine-grained Personal Access Token (PAT) under this machine user account with appropriate repository permissions (`repo` / `pull requests` / `issues` / `contents`).
- Set `GITHUB_TOKEN` in your `.env` file to this machine user PAT.

> **Why a Machine User?** GitHub prevents users from approving their own Pull Requests. By having automated graphs and agents operate under a dedicated machine user identity, human developers can review, comment on, and self-approve pull requests created by the system.

### 3. Running with Docker Compose

```bash
docker compose up --build -d
```

View live logs:
```bash
docker compose logs -f app
```

See [INSTALL.md](INSTALL.md) for more detailed installation and deployment instructions.
