# AI Code Review & QA Automation Platform

A portfolio-ready MVP for automated code review and QA reporting. The tool reads Git changes, runs automated tests, generates structured AI-style review feedback, and outputs a clean HTML report.

This project is positioned for Junior AI Engineer and AI Automation Engineer roles. It demonstrates practical automation, test orchestration, report generation, CI integration, and an architecture that can later connect to a real LLM API.

## Why This Is Useful

Modern teams need fast feedback before code reaches human reviewers. This tool helps by combining diff analysis, automated test execution, and structured review notes into one shareable report. The current version uses demo mode with structured AI review output, while the architecture is designed to support real LLM API integration later.

## Key Features

- Python CLI for local review automation
- Working-tree and commit-range Git diff detection
- Automatic test command detection for Python, Node.js, and .NET projects
- Pytest execution for the included sample project
- Demo-mode structured AI review output
- Pydantic schemas for review and test results
- GitHub-style HTML report generated with Jinja2
- GitHub Actions workflow that generates and uploads the report artifact

## Sample Report Screenshot

![AI Code Review Report](docs/screenshots/report-preview.png)

## Architecture / Workflow

```text
Developer pushes code or runs CLI
        |
        v
Git diff reader
        |
        +--> changed files
        +--> raw diff
        |
        v
Test runner detects project type
        |
        +--> pytest
        +--> npm test
        +--> dotnet test
        |
        v
Demo AI reviewer creates structured review JSON
        |
        v
Pydantic schemas validate results
        |
        v
Jinja2 report generator
        |
        v
HTML review report
```

## Tech Stack

- Python 3.11
- Pydantic
- Jinja2
- Pytest
- Git
- GitHub Actions

## Local Setup

```bash
git clone https://github.com/yy31104/ai-code-review-qa.git
cd ai-code-review-qa

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

## Review Modes

Demo mode is the default and does not require an API key:

```bash
AI_REVIEW_MODE=demo
```

To configure optional OpenAI review mode, copy the example env file and add your own API key:

```bash
cp .env.example .env
```

```text
AI_REVIEW_MODE=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
```

When `AI_REVIEW_MODE=openai`, the CLI sends the git diff and changed files to the OpenAI Responses API and validates the structured JSON response against the existing Pydantic `ReviewResult` schema. If the API key is missing, the API call fails, or the model response cannot be validated, the tool falls back to demo mode and includes a warning in the report summary.

## CLI Usage

### Local Working-Tree Review

Run the MVP review workflow against the included sample Python project:

```bash
python backend/app/main.py --repo ./sample-projects/python-demo --output backend/reports/review_report.html
```

This mode inspects the current uncommitted working-tree diff for the target project.

### Commit Range Review

Run the review against an explicit Git commit range:

```bash
python backend/app/main.py --repo . --base HEAD~1 --head HEAD --output backend/reports/review_report.html
```

This mode compares `<base>..<head>` and is the mode used by GitHub Actions.

Both modes will:

- inspect Git changes for the target project
- detect and run the appropriate test command
- create demo-mode structured AI review output
- generate an HTML review report

## GitHub Actions CI Usage

The workflow in `.github/workflows/ai-review.yml` runs on:

- push to `main`
- pull request to `main`
- manual `workflow_dispatch`

In CI, GitHub Actions checks out the repository, installs backend dependencies, runs the CLI with `--base HEAD~1 --head HEAD`, and uploads the generated report as an artifact named `review-report`.

## HTML Report Artifact

The generated report is saved to:

```text
backend/reports/review_report.html
```

It includes:

- project summary
- changed files
- risk level
- possible bugs
- missing tests
- suggested test cases
- security and reliability concerns
- automated test results
- recommended actions
- human review decision

The committed report is included as a sample portfolio artifact.

## Current MVP Status

Complete MVP:

- CLI workflow is implemented
- sample Python project is included
- automated tests run successfully
- HTML report generation works locally and in CI
- current AI review is demo mode with structured output

## Future Improvements

- Real LLM API integration
- GitHub PR diff support
- React dashboard
- ASP.NET Core API
- Docker packaging
- Historical review storage
