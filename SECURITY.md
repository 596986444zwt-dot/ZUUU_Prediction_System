# Security Policy / 安全报告

## Report privately

If you discover an API key, credential, token, private key, sensitive data exposure, or a vulnerability, **do not open a public Issue or Pull Request containing the evidence**. Do not paste credentials, Cookies, Authorization headers, raw datasets, production databases, or personal information into public discussions.

Use [GitHub Private Vulnerability Reporting](https://github.com/596986444zwt-dot/ZUUU_Prediction_System/security/advisories/new) to submit a private report to repository maintainers. Include affected commit/version, file paths and line numbers, impact, and sanitized reproduction steps. Keep any proof minimal; do not access, download, modify, or publish another person's data to demonstrate the problem.

If GitHub's private report form is temporarily unavailable, do not publish the evidence. You may open an Issue asking maintainers to restore private reporting, with **no vulnerability details or sensitive content**. No personal email address is designated as a project security contact.

发现 API Key、凭证、Token、私钥、敏感数据暴露或漏洞时，请通过上面的 GitHub 私密报告入口联系维护者，不要公开发证据。仅说明受影响版本、路径/行号、风险和脱敏复现过程。如果入口暂不可用，保留证据私密，可以发一个不含漏洞细节的“请求恢复私密报告”Issue。

## Scope and response

This is a research project without an LTS or guaranteed response-time commitment. Report issues against the current `main` and identify the affected commit. Maintainers will assess reports and coordinate remediation; reporters should wait for coordinated disclosure before publishing details. MIT warranty disclaimers remain applicable.

Do not use reporting as a reason to stop a production worker, alter Phase10 Soak, change model status, or edit runtime/forward-validation data. If a real credential is exposed, the credential owner should revoke/rotate it through its provider; deleting a visible file alone does not revoke access.
