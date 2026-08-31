# LangGraph Autonomous Agent System

An autonomous multi-agent orchestration system built with LangGraph, Discord integration, and tool automation.

---

## Table of Contents
1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Setup & Installation](#setup--installation)
4. [Machine User Configuration](#machine-user-configuration)
5. [Running the System](#running-the-system)
6. [Observability & Tracing](#observability--tracing)

---

## Overview

This system coordinates specialized autonomous agents to handle complex workflows including coding, project planning, research, wiki gardening, and task management. Agents interact via Discord channels and execute automated tool calls against local workspaces and external services.

---

## Prerequisites

- **Docker & Docker Compose** (recommended) or Python 3.11+
- **Git**
- **GitHub Account** (and a secondary account for machine user PAT)
- **Discord Developer Application** bot token(s)
- **Gemini / OpenAI / Anthropic API Keys**

---

## Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Create your environment configuration:**
   ```bash
   cp .env.example .env
   ```

3. **Configure required environment variables in `.env`** (refer to `.env.example` and the [Machine User Configuration](#machine-user-configuration) section below).

---

## Machine User Configuration

To enable human developers to review and self-approve Pull Requests created by the autonomous system, you must configure a **Machine User** Personal Access Token (PAT) for GitHub operations.

### Why a Machine User is Required
- GitHub security rules disallow authors from approving their own Pull Requests.
- If the system creates PRs using your primary human GitHub account token, you will be blocked from approving and merging those PRs via the standard review flow.
- By provisioning a secondary GitHub account (a "machine user" / bot account) and using its PAT for `GITHUB_TOKEN`, pull requests are authored by the machine user, allowing human developers to review, approve, and merge them seamlessly.

### Step-by-Step Setup:
1. **Create or select a dedicated GitHub account** to act as the machine user (e.g. `yourorg-bot` or `my-agent-bot`).
2. **Grant the machine user repository access**: Add the machine user account as a collaborator with write access to the target repository (or organization).
3. **Generate a Personal Access Token (PAT)**:
   - Log into the machine user account on GitHub.
   - Go to **Settings > Developer Settings > Personal Access Tokens > Fine-grained tokens** (or Tokens Classic).
   - Generate a token with permissions for **Repository contents (Read/Write)** and **Pull Requests (Read/Write)**.
4. **Set the environment variable in `.env`**:
   ```bash
   GITHUB_TOKEN=github_pat_your_machine_user_token_here
   ```
5. All automated GitHub operations (`gh` tool, branch creation, PR submission) will now be authenticated under the machine user identity.

---

## Running the System

### Using Docker Compose (Recommended)

```bash
# Build and start services in detached mode
docker compose up --build -d

# View live application logs
docker compose logs -f app

# Stop the services
docker compose down
```

For detailed Docker options and manual run instructions, see [INSTALL.md](INSTALL.md).

---

## Observability & Tracing

The platform integrates with [LangSmith](https://smith.langchain.com) for real-time observability:

1. Set `LANGSMITH_TRACING=true` and provide your `LANGSMITH_API_KEY` in `.env`.
2. Run the verification script:
   ```bash
   python scripts/verify_langsmith.py
   ```
3. View agent traces, execution graphs, and tool invocation latency in the LangSmith dashboard.
