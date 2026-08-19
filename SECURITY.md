# Security Policy

## Supported Versions

This is a sample project. Security fixes are applied to the `main` branch; there
are no maintained release branches.

## Reporting a Vulnerability

We take the security of this project seriously. If you believe you have found a
security vulnerability, please report it to us as described below.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them through one of the following methods:

1. **GitHub Security Advisories**: Use the
   [Security Advisories](https://github.com/aws-samples/sample-agentic-modernisation-integration-engine/security/advisories)
   feature to privately report a vulnerability.

2. **Email**: Send an email to the AWS Security team. See
   [AWS Vulnerability Reporting](https://aws.amazon.com/security/vulnerability-reporting/)
   for details.

### What to Include

Please include the following information in your report:

- Type of issue (e.g. path traversal, server-side request forgery, command injection, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

### Response Timeline

- **Initial Response**: Within 48 hours, we will acknowledge receipt of your report.
- **Status Update**: Within 7 days, we will provide an initial assessment.
- **Resolution**: We aim to resolve critical vulnerabilities within 30 days.

## Security Scanning

This project uses automated security scanning to identify vulnerabilities:

### Trivy Vulnerability Scanner

We use [Trivy](https://github.com/aquasecurity/trivy) to scan for:

- **Filesystem vulnerabilities**: Python and npm dependencies resolved from
  committed lockfiles
- **Configuration issues**: Misconfiguration in the Dockerfiles and
  `docker-compose.yml`
- **Secret detection**: Credentials accidentally committed to the working tree

Security scans run:
- On every push to the `main` branch
- On every pull request targeting `main`
- Weekly, because a new advisory can be published against an already-pinned
  dependency without the code changing

A CRITICAL or HIGH finding fails the check. Results are uploaded to the
[Security tab](https://github.com/aws-samples/sample-agentic-modernisation-integration-engine/security/code-scanning),
including on a failing run. See
[`.github/workflows/security.yml`](.github/workflows/security.yml).

### CodeQL Static Analysis

CodeQL runs via GitHub's default setup on every push to `main` and every pull
request, covering both Python and JavaScript/TypeScript. Findings appear as PR
review comments and in the repo's Security tab.

Default setup is configured in repo settings, not in a workflow file — adding a
workflow-based CodeQL job alongside it causes upload conflicts. If the team later
needs a wider query suite or custom queries, change the default setup
configuration, or toggle default setup off before adding an advanced workflow.

### Dependency Review

Pull requests are automatically checked for:
- Known vulnerabilities in dependencies
- License compliance issues
- Dependency version changes

### Secret Scanning

We use [gitleaks](https://github.com/gitleaks/gitleaks) to keep credentials out of
the repository (see [`.gitleaks.toml`](.gitleaks.toml) and
[`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml)):

- **On every pull request**: scans the PR's commit range; a detected secret fails
  the check so it can't merge.
- **Weekly (scheduled)**: a full-history sweep, to surface a secret that may
  predate the per-PR gate.

GitHub secret scanning with push protection is also enabled at the organization
level, which blocks a supported secret at push time.

If a secret ever does land, the rule of thumb is **rotate/revoke first**.
Rewriting history is a secondary, high-cost step that never substitutes for
rotation.

### Running Security Scans Locally

You can run the same scanners locally before committing:

```bash
# Install
brew install trivy gitleaks   # macOS

# Dependencies + secrets, at the same thresholds as the CI gate
trivy fs --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 .

# Dockerfile / docker-compose misconfiguration
trivy config --severity CRITICAL,HIGH --exit-code 1 .

# Secrets in the working tree
gitleaks detect --source . --config .gitleaks.toml --redact --verbose
```

> Keep those flags. Narrower options such as `--scanners vuln` make the local
> check weaker than the CI gate, so it passes locally and fails in CI.

Optionally, enable the pre-commit hook to scan staged changes before they enter a
commit:

```bash
uv tool install pre-commit   # or: pipx install pre-commit
pre-commit install
```

## Security Best Practices

When working on or deploying this project:

1. **Keep Dependencies Updated**: Merge Dependabot pull requests promptly to pick
   up security patches.

2. **Never Commit Credentials**: GitHub PATs and AWS credentials are inputs to
   this platform. Keep them in `.env` (excluded from git) for local development,
   and use an IAM execution role in deployment — never long-lived keys in an image
   or task definition. `.env.example` documents variable *names* with empty
   values.

3. **Secure API Access**: The services bind to localhost via Docker Compose by
   default. If exposing them externally, use proper authentication and TLS rather
   than `AUTH_DISABLED=true`.

4. **Review AI-Generated Output**: Generated code, plans and specifications are
   model output and are not trustworthy by default. Review before applying, and
   require human confirmation for any write action.

5. **Review Untrusted Input Paths**: Uploaded archives and user-supplied
   repository URLs are untrusted. Changes to archive extraction or repository
   cloning should be treated as security-relevant.

6. **Run Containers as Non-Root**: The Dockerfiles create an unprivileged user;
   keep it that way. The Trivy config scan checks this.

## Dependency Management

We actively monitor and update dependencies to address security vulnerabilities:

- **Dependabot**: Automated dependency updates via GitHub Dependabot
- **uv.lock / package-lock.json**: Locked dependency versions for reproducible
  builds
- **Regular Audits**: Periodic review of the dependency tree for security issues

## Security Updates

Security fixes are applied to `main` and documented in:

- [GitHub Security Advisories](https://github.com/aws-samples/sample-agentic-modernisation-integration-engine/security/advisories)

## License

This project is licensed under the MIT-0 License. See [LICENSE](LICENSE) for
details.
