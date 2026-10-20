import type { Locale } from "../lib/types";

export function roleFailureMessage(code: string | undefined, locale: Locale) {
  const zh = locale === "zh";
  if (code?.includes("deadline") || code?.includes("timeout")) return zh ? "该模型阶段已达到时间上限，尚未完成分析。" : "This model stage reached its deadline before completing the analysis.";
  if (code === "workbuddy_upstream_empty_stream" || code === "workbuddy_upstream_error") return zh ? "模型上游返回了明确错误，本阶段未完成。" : "The model upstream returned an explicit error; this stage did not complete.";
  if (code?.includes("structured_output") || code?.includes("result_missing")) return zh ? "未取得符合契约的结构化终态。" : "No contract-valid structured terminal result was received.";
  if (code === "runtime_cleanup_incomplete") return zh ? "所属进程清理尚未确认，暂不能重试。" : "Owned process cleanup is unconfirmed; retry remains blocked.";
  if (code === "runtime_native_transport_closed") return zh ? "SDK 连接在本阶段完成前关闭。" : "The SDK connection closed before this stage completed.";
  if (code === "runtime_native_rpc_error") return zh ? "SDK 请求被拒绝，请查看安全错误码。" : "The SDK request failed; check the safe error code.";
  if (code === "runtime_native_turn_failed") return zh ? "SDK 返回失败终态，本阶段未完成。" : "The SDK returned a failed terminal state; this stage did not complete.";
  return zh ? "本阶段未完成，请查看安全诊断；底层原因可能尚未确认。" : "This stage did not complete. Check safe diagnostics; the underlying cause may still be unknown.";
}

export default function RoleRuntimeDiagnostic({ locale, code, detail }: { locale: Locale; code?: string; detail?: Record<string, unknown> | null }) {
  const zh = locale === "zh";
  const safe: Record<string, unknown> = {};
  for (const name of ["budget_seconds", "elapsed_ms", "message_count", "assistant_message_count", "stream_event_count", "first_response_ms", "first_assistant_ms", "page_index"])
    if (typeof detail?.[name] === "number" && Number.isSafeInteger(detail[name]) && (detail[name] as number) >= 0) safe[name] = detail[name];
  for (const name of ["terminal_result_received", "sdk_connected", "query_submitted", "sdk_closing", "sdk_closed"])
    if (typeof detail?.[name] === "boolean") safe[name] = detail[name];
  for (const name of ["primary_failure_code", "cleanup_failure_code", "failure_category", "phase"])
    if (typeof detail?.[name] === "string" && /^[a-z_]{1,100}$/.test(detail[name] as string)) safe[name] = detail[name];
  const nativeCodes = new Set(["contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded", "rateLimitExceeded", "flexUnavailable", "serverOverloaded", "cyberPolicy", "misalignmentPolicyViolation", "tooManyDenials", "internalServerError", "unauthorized", "badRequest", "threadRollbackFailed", "sandboxError", "other", "httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts", "activeTurnNotSteerable"]);
  const nativeClasses = new Set(["CodexError", "JsonRpcError", "CodexRpcError", "ParseError", "InvalidRequestError", "MethodNotFoundError", "InvalidParamsError", "InternalRpcError", "ServerBusyError", "RetryLimitExceededError", "TransportClosedError", "RuntimeError", "TimeoutError", "unknown"]);
  if (typeof detail?.native_error_code === "string" && nativeCodes.has(detail.native_error_code)) safe.native_error_code = detail.native_error_code;
  if (typeof detail?.native_error_class === "string" && nativeClasses.has(detail.native_error_class)) safe.native_error_class = detail.native_error_class;
  if (typeof detail?.native_terminal_status === "string" && ["completed", "failed", "interrupted"].includes(detail.native_terminal_status)) safe.native_terminal_status = detail.native_terminal_status;
  if (typeof detail?.native_phase === "string" && ["sdk_initialize", "thread_start", "thread_resume", "turn_start", "message_stream"].includes(detail.native_phase)) safe.native_phase = detail.native_phase;
  if (typeof detail?.error_class === "string" && (nativeClasses.has(detail.error_class) || ["TypeError", "ValueError", "KeyError", "JSONDecodeError", "RuntimeContractError", "WorkBuddyError"].includes(detail.error_class))) safe.error_class = detail.error_class;
  if (typeof detail?.native_rpc_code === "number" && Number.isInteger(detail.native_rpc_code) && detail.native_rpc_code >= -(2 ** 31) && detail.native_rpc_code < 2 ** 31) safe.native_rpc_code = detail.native_rpc_code;
  return <div className="role-error" role="alert">
    <strong>{roleFailureMessage(code, locale)}</strong>
    {typeof safe.phase === "string" && <p>{zh ? "实际阶段" : "Stage"}: {safe.phase}</p>}
    {typeof safe.budget_seconds === "number" && <p>{zh ? "阶段预算" : "Stage budget"}: {safe.budget_seconds} s</p>}
    {typeof safe.elapsed_ms === "number" && <p>{zh ? "实际耗时" : "Elapsed"}: {Math.round(safe.elapsed_ms / 1000)} s</p>}
    <details><summary>{zh ? "技术详情（仅安全统计）" : "Technical details (safe statistics only)"}</summary><pre>{JSON.stringify({ failure_code: code && /^[a-z_]{1,100}$/.test(code) ? code : undefined, ...safe }, null, 2)}</pre></details>
  </div>;
}
