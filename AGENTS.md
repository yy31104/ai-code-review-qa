# AI Code Review & QA Automation Platform

Goal:
Build and maintain a portfolio-ready AI-assisted SDLC tool that analyzes Git diffs, detects possible bugs and missing tests, runs automated tests, and generates a structured HTML code review report.

Current project status:
- Python CLI MVP is complete
- Working-tree Git diff mode is supported
- Commit-range Git diff mode is supported with `--base` and `--head`
- Demo review mode works by default
- Optional OpenAI-powered structured review mode is implemented
- Pydantic validates review and test result schemas
- Jinja2 generates the HTML report
- Automated test detection supports Python, Node.js, and .NET projects
- GitHub Actions generates and uploads the report as an artifact
- Test output paths are sanitized for portfolio-friendly reports

Main commands:

Local working-tree review:

```bash
python backend/app/main.py --repo ./sample-projects/python-demo --output backend/reports/review_report.html