# Deploy Automation Platform (سامانه جامع اتوماسیون استقرار)

An enterprise-ready, open-source automation platform for DevOps and SRE teams. It unifies **ClickUp task orchestration**, **GitLab Git Flow deployment readiness auditing**, **Nginx Reverse Proxy & SSL (Certbot) automation**, **PostgreSQL provisioning**, and an interactive **Telegram Bot workflow** backed by an AI Decision Engine and a modern web monitoring dashboard.

---

## 🚀 Key Features

### 1. ClickUp Task Queue Processing & Automated State Resumption
- Automatically polls tasks in `new`, `in progress`, and `waiting for customer` statuses.
- Intelligently detects task progress milestones across 6 deployment phases:
  1. `Maintainer Access Granted`
  2. `Git Flow Deploy Readiness Passed`
  3. `.gitlab-ci.yml Deploy Jobs Configured`
  4. `docker-compose.yml Created`
  5. `Environment Variables, Database & Logging Configured`
  6. `Deployment Verified / Tested`
- Interactive resumption via CLI and Telegram bot from any milestone.

### 2. Deployment Readiness Auditing & Custom Policy Verification (AI-Assisted)
Automated compliance checker to ensure microservices and repositories adhere to project standards before deployment:
- **Customizable Requirements & Checklists**: Organizations can configure their own custom list of requirements (such as architecture docs, Git Flow branching, health checks, `/metrics` endpoints, CI/CD stages, SonarQube, test coverage, etc.).
- **AI-Powered or Rule-Based Validation**: The built-in AI assistant can evaluate developer compliance against your custom requirements prompt or rules, highlight any missing items, and generate constructive feedback comments directly on ClickUp or Telegram.
- **Automated Defect Commenting**: If requirements are unmet, it drafts polite, actionable remediation guides for developers and moves tasks to `waiting for customer`.

### 3. Nginx Reverse Proxy & SSL (Certbot) Automation
- Supports both system daemon (`/etc/nginx/sites-available`) and Docker container (`/srv`) environments.
- Automated SSL issuance via Certbot using **HTTP-01** and **DNS-01** challenges.
- Path-based routing and upstream proxying with automatic `nginx -t` validation and reloading.

### 4. PostgreSQL Database Provisioning
- Automated creation of databases, dedicated users, and cryptographically secure passwords.
- Remote `pg_hba.conf` configuration for application subnets with zero-downtime configuration reloads (`pg_reload_conf`).
- Automatic generation of connection strings and DSNs for environment files.

### 5. Telegram SRE Team Bot & Task Dispatcher
- Real-time notification and dispatching to individual topic threads in Telegram supergroups.
- Workload-aware task assignment using deterministic rules or LLM providers (OpenAI / Anthropic / Local Ollama).
- Ephemeral in-memory GitLab token management (zero disk persistence).
- Interactive inline buttons for approving deployments, requesting permissions, or reporting issues.

### 6. Liquid Glass Web Dashboard & Analytics
- Real-time web dashboard running on FastAPI (`http://localhost:8085/dashboard`).
- **My Tasks View**: Grouped by state with AI-powered task priority ranking.
- **SRE Forms Monitor**: Real-time overview of incoming deployment requests.
- **Analytics & Metrics**:
  - Assignee task distribution (donut chart).
  - Average lead time in `New` state by form type across selectable time ranges.
  - Weekly throughput tracker with daily breakdown.
  - **371-Day Activity Heatmap**: Contribution matrix with member filtering and dynamic quantile color scales.

#### 📸 Dashboard Preview

| **My Tasks View (Dual-Engine AI Priority)** | **SRE Forms Monitor** |
|:---:|:---:|
| ![My Tasks View](docs/screenshots/01_dashboard_my_tasks.png) | ![SRE Forms Monitor](docs/screenshots/02_dashboard_sre_forms.png) |

| **Analytics Dashboard & Contribution Heatmap** | **Task Details & AI Reasoning Modal** |
|:---:|:---:|
| ![Analytics Dashboard](docs/screenshots/03_dashboard_analytics.png) | ![Task Details Modal](docs/screenshots/04_dashboard_task_modal.png) |

---

## 📁 Architecture Overview

```
deploy_automation/
├── main.py                     # Application entrypoint (FastAPI, Bot, ClickUp loop)
├── config.py                   # Environment configuration (Pydantic Settings)
├── database.py                 # Async SQLite database (Sessions, Idempotency)
├── models.py                   # Shared data transfer objects (DTOs)
├── cli.py                      # Interactive terminal deployment wizard
├── api/
│   └── routes.py               # REST API endpoints & ClickUp webhook pipeline
├── bot/
│   ├── sre_manager_bot.py      # Primary multi-user Telegram bot
│   ├── telegram_service.py     # Single-admin notification bot
│   └── token_store.py          # Ephemeral RAM token store
├── engine/
│   ├── checker.py              # Deployment readiness and standards compliance checker
│   ├── decision_engine.py      # Rule-based and AI task assignment engine
│   ├── task_state_detector.py  # Deployment phase detector
│   ├── cicd_generator.py       # GitLab CI/CD deploy job generator
│   ├── compose_generator.py    # Docker Compose generator
│   └── nginx_parser.py         # Nginx configuration generator & parser
├── integrations/
│   ├── clickup_service.py      # ClickUp REST API client
│   ├── clickup_dispatcher.py   # Task monitoring & auto-assignment loop
│   ├── gitlab_service.py       # GitLab REST API client
│   ├── ai_provider.py          # Unified AI client (OpenAI / Anthropic compatible)
│   ├── ai_service.py           # AI task ranking & summary service
│   ├── nginx_service.py        # Remote SSH Nginx & SSL manager
│   ├── postgres_service.py     # Remote SSH PostgreSQL manager
│   └── ssh_service.py          # Paramiko SSH connection manager
└── static/                     # Web dashboard frontend (HTML/CSS/JS)
```

---

## ⚙️ Configuration (`.env`)

Copy `.env.example` to `.env` and configure your credentials:

```bash
cp .env.example .env
```

### Essential Settings:
```env
# ClickUp
CLICKUP_API_TOKEN=pk_your_token_here
CLICKUP_TEAM_ID=1234567
CLICKUP_SRE_SPACE_NAME=SRE
CLICKUP_DEPLOY_FORM_NAME=deploy

# GitLab
GITLAB_URL=https://gitlab.example.com
GITLAB_TOKEN=glpat-your_access_token_here

# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_ADMIN_CHAT_ID=123456789
TELEGRAM_TEAM_GROUP_ID=-1001234567890
SRE_TEAM_MEMBERS='[{"name": "Alice", "clickup_id": 1001, "telegram_id": 111111111, "topic_id": 1}]'

# AI Provider (Optional: openai or anthropic)
DECISION_MODE=rule_engine
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
```

---

## 🛠️ Quick Start

### Local Development
```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the service
python3 -m deploy_automation.main
```

The web dashboard will be accessible at:
```text
http://localhost:8085/dashboard
```

### Running with Docker Compose
```bash
docker compose up -d --build
docker compose logs -f
```

### Interactive CLI Deployment Tool
```bash
python3 -m deploy_automation.cli
```

---

## 🧪 Testing

Run the full automated test suite:
```bash
pytest
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
