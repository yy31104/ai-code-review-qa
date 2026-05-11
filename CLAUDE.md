# AI Code Review & QA Automation Platform

Goal:
Build a portfolio-ready AI-assisted SDLC tool that analyzes Git diffs, detects possible bugs and missing tests, runs automated tests, and generates a structured HTML report.

MVP scope:
- Python CLI only
- Read git diff and changed files
- Generate structured review JSON
- Run automated tests
- Generate HTML report with Jinja2
- Use mock LLM output first
- Add real LLM API later

Required command:
python backend/app/main.py --repo ./sample-projects/python-demo --output backend/reports/review_report.html

Required modules:
- git_diff_reader.py: collect git diff and changed files
- test_runner.py: detect project type and run pytest / dotnet test / npm test
- llm_reviewer.py: return structured JSON review, mock first
- schemas.py: define Pydantic models
- report_generator.py: generate HTML report
- main.py: orchestrate the workflow

Report sections:
- Project summary
- Changed files
- Risk level
- Possible bugs
- Missing tests
- Suggested test cases
- Security / reliability concerns
- Automated test results
- Recommended actions
- Human review decision

Rules:
- Do not build React dashboard yet
- Do not build ASP.NET API yet
- Do not add database yet
- Do not over-engineer
- Keep the MVP simple and working
