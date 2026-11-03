"""Run-bound native tool callbacks; no shell, files, credentials or new SAP reader."""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import contextvars
import json
import threading
import time


class NativeBrokerTools:
    namespace = "sapba_broker"

    def __init__(self, codex, broker, run_id, capability, *, deadline):
        from .mcp_server import _SAP_TOOLS
        from .runtime_report_contract import tools_for_contract
        from .runtime_input_control import tools_for_task
        contract = broker.report_contract(run_id)
        state = broker.store.get_harness_state(run_id)
        tools = tools_for_task(tools_for_contract(_SAP_TOOLS, contract),
            formal=contract.get("acceptance_spec") is not None,
            version=state.get("terminal_protocol", 1))
        self.names = frozenset(item["name"] for item in tools)
        self.specs = [{"type": "namespace", "name": self.namespace,
            "description": "This run's approved read-only SAP Broker and evidence/report gates.",
            "tools": [{"type": "function", "name": item["name"],
                "description": item["description"], "inputSchema": copy.deepcopy(item["inputSchema"]),
                "deferLoading": False} for item in tools]}]
        self.client = codex._client._sync
        self.broker, self.run_id, self.capability = broker, run_id, capability
        self.deadline = deadline
        self.loop = asyncio.get_running_loop()
        self.context = contextvars.copy_context()
        self.thread_id = None
        self.closed = False
        self.pending = set()
        self.tasks = set()
        self.lock = threading.Lock()
        self.previous_request = self.client.request
        self.previous_handler = self.client._approval_handler

    def install(self):
        # SDK 0.163's generated public wrapper omits experimental dynamicTools.
        # Preserve its normal thread parameters and inject only the reviewed
        # field from this exact CLI's experimental protocol schema.
        def request(method, params, *args, **kwargs):
            if method in {"thread/start", "thread/resume"}:
                if self.closed or self.thread_id:
                    raise RuntimeError("runtime_broker_thread_binding_invalid")
                params = copy.deepcopy(params or {})
                if method == "thread/start":
                    params["dynamicTools"] = copy.deepcopy(self.specs)
                elif not isinstance(params.get("threadId"), str) or not params["threadId"]:
                    raise RuntimeError("runtime_broker_thread_binding_invalid")
                result = self.previous_request(method, params, *args, **kwargs)
                if method == "thread/resume" and result.thread.id != params["threadId"]:
                    raise RuntimeError("runtime_broker_thread_binding_invalid")
                self.thread_id = result.thread.id
                return result
            return self.previous_request(method, params, *args, **kwargs)
        self.client.request = request
        self.client._approval_handler = self.handle
        return self

    @staticmethod
    def response(value):
        return {"success": value.get("ok") is not False,
            "contentItems": [{"type": "inputText", "text": json.dumps(value, ensure_ascii=False)}]}

    def accepts_item(self, item):
        return (not self.closed and item.get("namespace") == self.namespace
            and item.get("tool") in self.names)

    def handle(self, method, params):
        if method != "item/tool/call":
            return self.previous_handler(method, params)
        params = params if isinstance(params, dict) else {}
        if (self.closed or params.get("threadId") != self.thread_id or not self.thread_id
                or params.get("namespace") != self.namespace or params.get("tool") not in self.names
                or not isinstance(params.get("arguments"), dict)
                or not isinstance(params.get("callId"), str) or not params["callId"]
                or not isinstance(params.get("turnId"), str) or not params["turnId"]):
            return self.response({"ok": False, "code": "runtime_broker_tool_binding_denied"})
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return self.response({"ok": False, "code": "runtime_deadline_exceeded"})
        arguments = copy.deepcopy(params["arguments"])
        arguments["tool_call_id"] = params["callId"]
        async def invoke():
            if self.closed:
                return {"ok": False, "code": "runtime_broker_tool_binding_denied"}
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                return await self.broker.handle(self.run_id, self.capability, params["tool"], arguments)
            finally:
                self.tasks.discard(task)
        future = self.context.copy().run(asyncio.run_coroutine_threadsafe, invoke(), self.loop)
        with self.lock:
            if self.closed:
                future.cancel()
                return self.response({"ok": False, "code": "runtime_broker_tool_binding_denied"})
            self.pending.add(future)
        try:
            value = future.result(timeout=min(remaining, 330))
            return self.response(value if isinstance(value, dict) else
                {"ok": False, "code": "runtime_broker_response_invalid"})
        except concurrent.futures.TimeoutError:
            future.cancel()
            return self.response({"ok": False, "code": "runtime_broker_tool_timeout"})
        except Exception:
            return self.response({"ok": False, "code": "runtime_broker_tool_failed"})
        finally:
            with self.lock:
                self.pending.discard(future)

    async def aclose(self):
        with self.lock:
            self.closed = True
            pending = list(self.pending)
        for future in pending:
            future.cancel()
        self.client.request = self.previous_request
        self.client._approval_handler = self.previous_handler
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending_tasks = await asyncio.wait(tasks, timeout=5)
            if pending_tasks:
                from .runtime_execution import RuntimeExecutionError
                raise RuntimeExecutionError("runtime_cleanup_incomplete")
            await asyncio.gather(*done, return_exceptions=True)
