# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in **AuroraMic**, please **do not** disclose it in a public GitHub issue. Instead, report it privately using **GitHub Security Advisories / Private Vulnerability Reporting** on the repository's **Security** tab (or contact the repository maintainers privately).

Please include:

- A clear description of the issue and potential impact
- Steps to reproduce or a minimal proof of concept
- Windows version, Python version, and any relevant configuration details (with secrets removed)

We aim to acknowledge reports promptly and work with you on a fix before coordinated disclosure.

## Secret & Data Hygiene

- **Never commit `.env` or API keys**: Keep your Groq key in `.env` (which is ignored by `.gitignore`) and never paste real API keys or bearer tokens into issues, pull requests, screenshots, or commit messages.
- **Never commit log files**: Local `*.log` files are ignored by `.gitignore`; review any logs carefully before sharing them in bug reports.
- **Key redaction**: `GroqTranscriber` and error sanitizers redact `GROQ_API_KEY` before raising or logging runtime errors—please preserve this behavior in all changes.
