/* Browser state checks: mocked fetch/DOM; no network or hardware. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../vision_console.js'), 'utf8');

async function page() {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: 'auto', disabled: false, textContent: '',
      replaceChildren() {}, appendChild() {}});
    return nodes.get(id);
  };
  const calls = [];
  const state = {frame: 0, failFreeze: false, mode: {armed: true, kind: 'block_grasp', session_id: 's1'}};
  const context = vm.createContext({document: {getElementById: node, createElement: () => ({})},
    Date, setTimeout() {}, setInterval() {}, fetch: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      calls.push({url, body});
      let data;
      if (url === '/api/status') data = {mode: state.mode, health: {mode: 'real'}};
      else if (url.startsWith('/api/freeze')) {
        if (state.failFreeze) data = {error: 'no candidate'};
        else {
          state.frame++;
          data = {freeze_id: 'freeze' + state.frame, frame_id: 'frame' + state.frame,
            session_id: 's1', chosen: {center: [20, 20]}, payload: {center: [20, 20]}, joints: []};
        }
      } else if (url === '/api/grasp') {
        data = state.submit ? await state.submit() : {response: {job: 'ok'}};
      } else throw Error('Unexpected URL: ' + url);
      return {ok: true, json: async () => data};
    }});
  vm.runInContext(source, context);
  await context.status();
  return {context, node, calls, state};
}

(async () => {
  {
    const p = await page();
    await p.node('freeze').onclick();
    await p.node('grasp').onclick(); // First confirmation.
    await p.node('freeze').onclick(); // A new preview must cancel confirmation.
    await p.node('grasp').onclick();
    assert.equal(p.calls.filter(x => x.url === '/api/grasp').length, 0);
    await p.node('grasp').onclick();
    const submission = p.calls.filter(x => x.url === '/api/grasp');
    assert.equal(submission.length, 1);
    assert.deepEqual(submission[0].body, {freeze_id: 'freeze2', frame_id: 'frame2', session_id: 's1'});
    assert.equal(p.node('grasp').disabled, true);
  }
  {
    const p = await page();
    await p.node('freeze').onclick();
    p.state.failFreeze = true;
    await p.node('freeze').onclick();
    await p.node('grasp').onclick();
    await p.node('grasp').onclick();
    assert.equal(p.node('grasp').disabled, true);
    assert.equal(p.calls.filter(x => x.url === '/api/grasp').length, 0);
  }
  {
    const p = await page();
    await p.node('freeze').onclick();
    await p.node('grasp').onclick();
    let release;
    p.state.submit = () => new Promise(resolve => {release = resolve;});
    const submitting = p.node('grasp').onclick();
    await p.context.status(); // Polls must not re-enable an in-flight submission.
    assert.equal(p.node('grasp').disabled, true);
    assert.equal(p.node('freeze').disabled, true);
    await p.node('grasp').onclick();
    await p.node('freeze').onclick();
    assert.equal(p.calls.filter(x => x.url === '/api/grasp').length, 1);
    assert.equal(p.calls.filter(x => x.url.startsWith('/api/freeze')).length, 1);
    release({error: 'collision rejected; evidence saved'});
    await submitting;
    assert.match(p.node('msg').textContent, /collision rejected/);
    assert.equal(p.node('grasp').disabled, true);
  }
  {
    const p = await page();
    await p.node('freeze').onclick();
    await p.node('grasp').onclick();
    p.node('label').onchange();
    assert.equal(p.node('grasp').disabled, true);
    p.state.mode = {armed: true, kind: 'gesture_dance', session_id: 's2'};
    await p.context.status();
    assert.equal(p.node('grasp').disabled, true);
  }
  console.log('8099 console: exact preview binding, new-preview confirmation, failure invalidation and duplicate prevention passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
