/* One preview, one explicitly confirmed submission of that exact preview. */
'use strict';
let frozen = null, mode = null, busy = false, armedClick = null;
let pollVersion = 0;
const el = id => document.getElementById(id);
function setMsg(text) { el('msg').textContent = text; }
function resetConfirm() {
  armedClick = null;
  el('grasp').textContent = '② 开始抓取（会运动）';
}
function renderButtons() {
  el('freeze').disabled = busy || !!mode?.busy;
  el('label').disabled = busy;
  el('grasp').disabled = busy || !frozen || !mode?.armed ||
    mode.kind !== 'block_grasp' || !!mode.busy || frozen.session_id !== mode.session_id;
}
function clearPreview() {
  frozen = null;
  resetConfirm();
  renderButtons();
}
async function json(url, body) {
  const response = await fetch(url, {cache: 'no-store', ...(body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  })});
  const data = await response.json();
  if (!response.ok || data.error) throw Error(data.error || '服务请求失败');
  return data;
}
async function status() {
  const version = ++pollVersion;
  try {
    const data = await json('/api/status');
    if (version !== pollVersion) return;
    mode = data.mode ? {...data.mode, busy: data.busy || data.mode.busy} : null;
    const health = data.health || {};
    el('session').textContent = mode?.armed
      ? `已开启 · ${mode.kind} · ${mode.task_mode || ''} · 已用 ${mode.used}/${mode.max_actions} · 剩 ${mode.expires_in}s · 下限 ${mode.min_score} · 会话 ${mode.session_id}`
      : '未开启（请先在板端 /modes 开启方块识别自动抓取）';
    el('health').textContent = `mode=${health.mode} · camera=${health.camera?.connected} · busy=${data.busy || mode?.busy}`;
    if (frozen && (!mode?.armed || mode.session_id !== frozen.session_id)) clearPreview();
  } catch (error) {
    if (version !== pollVersion) return;
    mode = null;
    clearPreview();
    el('health').textContent = '连接失败：' + error.message;
  }
  renderButtons();
}
el('reload').onclick = status;
el('label').onchange = clearPreview;
el('freeze').onclick = async () => {
  if (busy) return;
  clearPreview();
  busy = true;
  renderButtons();
  setMsg('冻结中…');
  el('shot').replaceChildren();
  try {
    const label = el('label').value;
    const result = await json('/api/freeze?label=' + encodeURIComponent(label), {});
    if (!result.chosen || !result.freeze_id || !result.frame_id) throw Error('没有可用的冻结目标');
    frozen = {freeze_id: result.freeze_id, frame_id: result.frame_id, session_id: result.session_id};
    setMsg(`frame_id=${result.frame_id}\n关节：${result.joints}\n所选：${JSON.stringify(result.chosen)}\n提交载荷：${JSON.stringify(result.payload)}\n记录：${result.evidence_dir}`);
    if (result.overlay) {
      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + result.overlay;
      el('shot').appendChild(img);
    }
  } catch (error) {
    clearPreview();
    setMsg('冻结失败：' + error.message);
  } finally {
    busy = false;
    await status();
    renderButtons();
  }
};
el('grasp').onclick = async () => {
  if (el('grasp').disabled || busy || !frozen) return;
  const now = Date.now();
  if (!armedClick || armedClick.id !== frozen.freeze_id || now - armedClick.at > 6000) {
    const confirmation = armedClick = {id: frozen.freeze_id, at: now};
    el('grasp').textContent = '② 再点一次确认（6 秒内）';
    setMsg('这一步会让机械臂运动。请在 6 秒内再点一次同一按钮确认。');
    setTimeout(() => { if (armedClick === confirmation) resetConfirm(); }, 6100);
    return;
  }
  const request = {...frozen};
  busy = true;
  clearPreview();
  setMsg('提交中…');
  try {
    const result = await json('/api/grasp', request);
    setMsg('已受理：' + JSON.stringify(result.response, null, 1) + '\n记录：' + result.evidence_dir);
  } catch (error) {
    setMsg('提交结果：' + error.message);
  } finally {
    busy = false;
    await status();
    renderButtons();
  }
};
status();
setInterval(status, 2000);
