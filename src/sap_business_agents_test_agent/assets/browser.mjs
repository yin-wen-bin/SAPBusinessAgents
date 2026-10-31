// Deterministic browser executor. No model SDK, arbitrary evaluate, or external writes.
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';

const allowedActions = new Set(['navigate','click','fill','select','wait_for','expect_text','expect_count','screenshot','capture_json','capture_table']);
const pointer = (value, address = '') => address.split('/').filter(Boolean).reduce((v,k) => v?.[k.replace(/~1/g,'/').replace(/~0/g,'~')], value);
const localPath = (origin, value) => {
  if(typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) throw Error('relative_path_required');
  const result = new URL(value, origin);
  if(result.origin !== origin) throw Error('cross_origin_path_rejected');
  return result.href;
};

export async function run(configurationPath, caseId) {
  const config = JSON.parse(await readFile(configurationPath, 'utf8'));
  if(config.case.id !== caseId) throw Error('case_identity_mismatch');
  const {case: spec, target, output} = config;
  const allowedOrigins = new Set([new URL(target.site_url).origin, new URL(target.api_url).origin]);
  await mkdir(output, {recursive:true});
  let browser, context, page;
  const evidence = [], errors = [], escaped = [], consoleMessages = [];
  let result = {status:'passed', code:'checks_passed', evidence};
  try {
    const require = createRequire(import.meta.url);
    const {chromium} = require(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
    browser = await chromium.launch({headless:true, channel:process.env.SAPBA_BROWSER_CHANNEL || 'msedge'});
    context = await browser.newContext({viewport:spec.viewport || {width:1440,height:900}});
    await context.tracing.start({screenshots:true, snapshots:true});
    page = await context.newPage();
    page.setDefaultTimeout(Math.min(spec.action_timeout_ms || 30000, 120000));
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => consoleMessages.push({type:message.type(),text:message.text()}));
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if(!allowedOrigins.has(url.origin)) {
        escaped.push({origin:url.origin,method:request.method()});
        return route.abort();
      }
      const fixture = (spec.fixtures || []).find(item => item.path === url.pathname && (item.method || 'GET') === request.method());
      if(fixture) return route.fulfill({status:fixture.status || 200,contentType:'application/json',body:JSON.stringify(fixture.body)});
      if(spec.mode === 'simulated' && request.method() !== 'GET') {
        escaped.push({origin:url.origin,path:url.pathname,method:request.method()});
        return route.abort();
      }
      if(/\/(integration-actions|authentication|sdk-runtimes|sdks|plugins)(\/|$)/.test(url.pathname) && request.method() !== 'GET') {
        escaped.push({origin:url.origin,path:url.pathname,method:request.method()});
        return route.abort();
      }
      // Product mutation goes only to the isolation-verified target, not real mail/accounts.
      return route.continue();
    });
    const locate = action => {
      if(action.role) return page.getByRole(action.role, {name:action.name, exact:action.exact ?? true});
      if(action.label) return page.getByLabel(action.label, {exact:action.exact ?? true});
      if(action.test_id) return page.getByTestId(action.test_id);
      if(action.selector) return page.locator(action.selector);
      throw Error('locator_required');
    };
    for(const action of spec.actions) {
      if(!allowedActions.has(action.type)) throw Error('browser_action_invalid');
      switch(action.type) {
        case 'navigate': await page.goto(localPath(target.site_url, action.path)); break;
        case 'click': await locate(action).click(); break;
        case 'fill': await locate(action).fill(String(action.value)); break;
        case 'select': await locate(action).selectOption(String(action.value)); break;
        case 'wait_for': await locate(action).waitFor({state:action.state || 'visible'}); break;
        case 'expect_text': {
          const text = await locate(action).innerText();
          if(action.exact ? text.trim() !== action.text : !text.includes(action.text)) throw Error('text_assertion_failed');
          break;
        }
        case 'expect_count': if(await locate(action).count() !== action.count) throw Error('count_assertion_failed'); break;
        case 'screenshot': {
          const name = `step-${evidence.length}.png`;
          await page.screenshot({path:path.join(output,name),fullPage:true}); evidence.push(name); break;
        }
        case 'capture_json': {
          const response = await page.request.get(localPath(target.api_url, action.path));
          if(!response.ok()) throw Error('observation_request_failed');
          const body = await response.json();
          const observation = {rows:pointer(body,action.rows_pointer),
            source_complete:pointer(body,action.complete_pointer) === true,
            source_truncated:action.truncated_pointer ? pointer(body,action.truncated_pointer) === true : false,
            scope:action.scope, captured_at:new Date().toISOString()};
          if(!Array.isArray(observation.rows)) throw Error('observation_rows_invalid');
          await writeFile(path.join(output,'observation.json'),JSON.stringify(observation,null,2));
          evidence.push('observation.json'); break;
        }
        case 'capture_table': {
          const rows = [];
          for(const row of await locate(action).all()) {
            const value = {};
            for(const [field,selector] of Object.entries(action.fields)) value[field] = (await row.locator(selector).innerText()).trim();
            rows.push(value);
          }
          const observation = {rows, scope:action.scope, source_complete:await page.locator(action.complete_selector).count() === 1,
            source_truncated:false, captured_at:new Date().toISOString()};
          await writeFile(path.join(output,'observation.json'),JSON.stringify(observation,null,2)); evidence.push('observation.json'); break;
        }
      }
    }
    if(errors.length || escaped.length) throw Error(errors.length ? 'page_javascript_error' : 'external_request_rejected');
  } catch(error) {
    result = {status:!browser ? 'blocked' : 'failed',
      code:error.code === 'MODULE_NOT_FOUND' ? 'playwright_unavailable' : !browser ? 'browser_unavailable' : /^[a-z_]+$/.test(error.message) ? error.message : 'browser_assertion_failed', evidence};
    if(page) { try {await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});evidence.push('failure.png');} catch {} }
  } finally {
    if(context) {try {await context.tracing.stop({path:path.join(output,'trace.zip')});evidence.push('trace.zip');} catch {} }
    if(browser) await browser.close();
    await writeFile(path.join(output,'browser-diagnostics.json'),JSON.stringify({page_errors:errors,console:consoleMessages,external_requests:escaped},null,2));
    evidence.push('browser-diagnostics.json');
    await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
    if(result.status !== 'passed') process.exitCode = 1;
  }
  return result;
}
