import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createContext, runInContext} from 'node:vm';

const source = await readFile(new URL('../src/components/RunConsole.astro', import.meta.url), 'utf8');
const script = source.slice(source.indexOf('  const feedbackStepIndex ='), source.indexOf('  const renderIntegrationActions ='));

function harness() {
  const node = () => ({hidden: true, dataset: {}, textContent: '', replaceChildren() {}, removeAttribute(name) {delete this[name];}});
  const nodes = new Map();
  const steps = Array.from({length: 4}, () => {
    const classes = new Set();
    return {classes, classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);}}};
  });
  const root = {querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  }};
  const panel = root.querySelector('[data-feedback-progress]');
  panel.querySelector = root.querySelector;
  panel.querySelectorAll = () => steps;
  const calls = [], alerts = [], streams = [];
  const question = '请确认是否仅按 RE 凭证类型统计。';
  let feedback = {feedback_request_id: 'feedback-1', status: 'reviewing', phase: 'runtime_review'};
  let session = {session_id: 'session-1', status: 'reviewing_feedback', current_iteration: 1,
    original_query: '供应商未清发票校验', iterations: [], active_feedback_request: feedback};
  class EventSource {
    static CLOSED = 2;
    readyState = 1;
    listeners = new Map();
    constructor() {streams.push(this);}
    addEventListener(name, listener) {this.listeners.set(name, listener);}
    close() {this.readyState = EventSource.CLOSED;}
  }
  const context = createContext({root, EventSource, window: {EventSource}, locale:'zh', recoverySubmission:null, recoveryMessage:()=> '',
    labels: {status: {reviewing_feedback: '正在分析反馈', waiting_input: '等待补充信息'},
      freeQuerySession: {feedbackSteps: ['已提交', '理解反馈', '判断查询', '准备结果'], waiting: '等待补充信息'}},
    terminal: new Set(['completed', 'inconclusive', 'failed', 'cancelled']),
    fetch: async url => {calls.push(url); return {ok: true, json: async () => url.includes('/feedback-requests/') ? feedback : session};},
    showAlert: message => alerts.push(message),
  });
  runInContext(`let currentSession, activeFeedbackRequestId = '', feedbackSource;
    const sessionId = 'session-1', apiBase = 'http://test';
    ${script}
    globalThis.ui = {renderFreeQuerySession, connectFeedback, getSession: () => currentSession};`, context);
  context.ui.renderFreeQuerySession(session);
  context.ui.connectFeedback('feedback-1');
  return {nodes, steps, calls, alerts, question, ui: context.ui,
    async emit(name) {await streams.at(-1).listeners.get(name)();},
    waiting() {
      feedback = {...feedback, status: 'waiting_input', phase: 'deciding', decision: {clarification_question: question}};
      session = {...session, status: 'waiting_input', active_feedback_request: feedback, pending_feedback: feedback};
    },
  };
}

test('feedback waiting-input event reveals the clarification form without reloading the page', async () => {
  const h = harness();
  assert.equal(h.nodes.get('[data-session-feedback]').hidden, false);
  assert.equal(h.nodes.get('[data-session-feedback] button[type=submit]').disabled, true);
  h.waiting();
  await h.emit('feedback_waiting_input');
  assert.equal(h.ui.getSession().status, 'waiting_input');
  assert.equal(h.nodes.get('[data-session-status]').textContent, '等待补充信息');
  assert.equal(h.nodes.get('[data-session-feedback]').hidden, false);
  assert.equal(h.nodes.get('[data-session-feedback]').dataset.mode, 'clarification');
  assert.equal(h.nodes.get('[data-session-feedback] button[type=submit]').disabled, false);
  assert.equal(h.nodes.get('[data-session-feedback-question]').textContent, h.question);
  assert.equal(h.nodes.get('[data-feedback-progress-detail]').textContent, h.question);
  assert.equal(h.steps.some(step => step.classes.has('is-active')), false);
  assert.deepEqual(h.alerts, []);
  assert.deepEqual(h.calls, [
    'http://test/api/free-query-sessions/session-1/feedback-requests/feedback-1',
    'http://test/api/free-query-sessions/session-1',
  ]);
});

test('ongoing feedback review keeps its progress state and does not expose a clarification form', async () => {
  const h = harness();
  await h.emit('feedback_review_started');
  assert.equal(h.ui.getSession().status, 'reviewing_feedback');
  assert.equal(h.nodes.get('[data-session-feedback]').hidden, false);
  assert.equal(h.nodes.get('[data-session-feedback] button[type=submit]').disabled, true);
  assert.equal(h.steps[1].classes.has('is-active'), true);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.alerts, []);
});
