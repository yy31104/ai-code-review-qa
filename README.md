# AI Code Review & QA Automation Platform

A portfolio-ready MVP for automated code review and QA reporting. The tool reads Git changes, runs automated tests, generates structured AI-style review feedback, and outputs a clean HTML report.

This project is positioned for Junior AI Engineer and AI Automation Engineer roles. It demonstrates practical automation, test orchestration, report generation, CI integration, and an architecture that can later connect to a real LLM API.

## Why This Is Useful

Modern teams need fast feedback before code reaches human reviewers. This tool helps by combining diff analysis, automated test execution, and structured review notes into one shareable report. The current version uses demo mode with structured AI review output, while the architecture is designed to support real LLM API integration later.

## Key Features

- Python CLI for local review automation
- Git diff and changed-file detection
- Automatic test command detection for Python, Node.js, and .NET projects
- Pytest execution for the included sample project
- Demo-mode structured AI review output
- Pydantic schemas for review and test results
- GitHub-style HTML report generated with Jinja2
- GitHub Actions workflow that generates and uploads the report artifact

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

## CLI Usage

Run the MVP review workflow against the included sample Python project:

```bash
python backend/app/main.py --repo ./sample-projects/python-demo --output backend/reports/review_report.html
```

The command will:

- inspect Git changes for the target project
- detect and run the appropriate test command
- create demo-mode structured AI review output
- generate an HTML review report

## GitHub Actions CI Usage

The workflow in `.github/workflows/ai-review.yml` runs on:

- push to `main`
- pull request to `main`
- manual `workflow_dispatch`

In CI, GitHub Actions checks out the repository, installs backend dependencies, runs the CLI, and uploads the generated report as an artifact named `review-report`.

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

## Sample Report Screenshot

Screenshot placeholder:

```text
docs/images/sample-report-screenshot.png
```

Add a screenshot of `backend/reports/review_report.html` here when preparing the final portfolio presentation.

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
