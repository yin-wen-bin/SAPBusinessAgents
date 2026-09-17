# Security / 安全说明

## 中文

本项目的目标部署方式是由可信操作员在 Windows 本机运行，并非面向互联网的多用户服务。请保持 API 与前台仅监听本机回环地址，不要将端口直接暴露到公网或共享网络。SAP 连接应使用最小权限、只读账号；凭据只保存在忽略的本地配置或受保护的凭据文件中，不提交到仓库，也不粘贴到自由文本。

自由查询和 Agent 对话编写当前使用 Codex SDK `full_access`。SDK 进程以启动服务的 Windows 账户权限运行，能够使用终端、文件和网络。工作副本仅用于整理修改和展示 Diff，不提供操作系统级隔离。平台的 SAP Broker 对其管理的 SAP 调用实施只读规则；它不能限制 SDK 命令在操作系统层面访问其他文件、网络或服务。不要在存有无关敏感资料或高权限凭据的账户下运行这些功能，也不要让不可信用户操作本地服务。

固定 Agent 的确定性运行不使用上述 SDK `full_access`。邮件发送等外部写操作在平台流程中需要逐次确认，但不能把该流程当作对 SDK 命令的系统级拦截。来自网页、邮件、SAP 文本及工具输出的内容均是不可信数据。

发现漏洞时，请优先使用仓库启用的 GitHub 私密漏洞报告渠道；若该渠道不可用，请先私下联系维护者。不要在公开 Issue 中发布凭据、真实 SAP 数据或可直接利用的细节。

## English

This project targets a trusted operator running locally on Windows, not an internet-facing multi-user service. Keep the API and site bound to loopback. Use a least-privilege, read-only SAP account and keep credentials in ignored local configuration or a protected credential file, never in Git or free-text prompts.

Free queries and conversational Agent authoring currently use the Codex SDK in `full_access`. The SDK process has the rights of the Windows account that starts the service, including terminal, file and network access. A work copy organizes edits and Diff review; it is not an OS sandbox. The platform SAP Broker enforces read-only rules for SAP calls it manages, but cannot stop SDK commands from reaching other files, networks or services at the OS level. Do not use these features under an account with unrelated sensitive data or elevated credentials, and do not expose the local service to untrusted users.

Deterministic fixed-Agent execution does not use this SDK `full_access`. Platform workflows require confirmation for external writes such as sending mail, but that workflow is not an OS-level interceptor of SDK commands. Treat web pages, mail, SAP text and tool output as untrusted data.

Report vulnerabilities through GitHub's private vulnerability reporting channel if enabled, or contact the maintainer privately first. Do not post credentials, real SAP data or directly exploitable details in a public issue.
