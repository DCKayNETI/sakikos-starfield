/**
 * 乐团世界书 · Google Drive → GitHub 实时同步触发器
 * =====================================================
 * 部署位置：Google Apps Script（独立项目，用可访问公开区文档的账号运行）
 * 作用：文档被修改后，向 GitHub 发送 repository_dispatch 信号，
 *       触发 sync_gdrive.yml 立即拉取并重新部署（代替不可靠的 GitHub cron）。
 *
 * ── 部署步骤 ──
 * 1. script.google.com 新建项目，粘贴本文件全部代码；
 * 2. 项目设置 → 脚本属性，新增：
 *        GITHUB_PAT = <Fine-grained PAT>
 *    （PAT 权限：仅 sakikos-starfield 仓库、Repository permissions → Contents: Read and write）
 * 3. 运行一次 setupTriggers()，授权后创建每 5 分钟轮询触发器；
 * 4. 运行一次 testDispatch() 自检（日志出现「响应码: 204」即成功）；
 * 5. GitHub 侧无需改动：sync_gdrive.yml 已监听 repository_dispatch [gdrive_update]。
 *
 * 说明：轮询的是固定文档 ID 列表，不依赖文件夹归属；新增月度卷时只需编辑
 * 「编年史卷索引」文档（已列入轮询），索引一变就会触发同步，无需改代码。
 */

var POLL_DOC_IDS = [
  '1TpwLPKhmtwAGInf2WfUYyFoQsL3QO4gmVIO4zFOfGYo', // 乐团世界书
  '1uMkTfLos3ia8QAOoKPY5ixFA7eSs87LmBhpR0idFKbA', // 编年史 2026-10 卷
  '1RXOIuV9Sehhm1rbNhIZbIKCw-T8neRlCE13M-gDL7dA', // 编年史 2026-11 卷
  '1k3RmSwz9G8zCDk97fut72KAfh3odRNt3SQ9fxlQglik', // 乐团共享日记本
  '1QixMpZwdImV1a65hx8jr5j_mWWPAIOeJv9FSxlp4TgY', // 创作者世界观随想
  '1bMIVQ31xip0fegW6EsIheaRxZ29qjmDSJyofBU6KKw8', // 未来演进计划与版本发布日志
  '1g_EZcSdyiZeRY_BAkJYnESBTy8E1IThea89NPqc_l7A'  // 编年史卷索引（新卷上线会改动它）
];
var GITHUB_REPO = 'dckayneti/sakikos-starfield';
var SNAPSHOT_KEY = 'gdrive_last_updated_snapshot';
var LAST_DISPATCH_KEY = 'last_dispatch_ms';
var MIN_DISPATCH_GAP_MS = 60 * 1000; // 60 秒内最多派发一次，避免多文档连续保存导致连发

/** 首次部署时手动运行一次：创建 5 分钟轮询触发器 */
function setupTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var fn = t.getHandlerFunction();
    if (fn === 'checkDocUpdates' || fn === 'checkFolderUpdates') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('checkDocUpdates').timeBased().everyMinutes(5).create();
  Logger.log('已创建 5 分钟轮询触发器（checkDocUpdates）。');
}

/** 轮询入口：对比各文档 lastUpdated，有变化才派发 */
function checkDocUpdates() {
  var props = PropertiesService.getScriptProperties();
  var snapshot = {};
  try {
    snapshot = JSON.parse(props.getProperty(SNAPSHOT_KEY) || '{}');
  } catch (err) { snapshot = {}; }

  var current = {};
  var changed = [];
  POLL_DOC_IDS.forEach(function (id) {
    try {
      current[id] = DriveApp.getFileById(id).getLastUpdated().getTime();
      if (snapshot[id] && snapshot[id] !== current[id]) changed.push(id);
    } catch (err) {
      Logger.log('无法读取文档 ' + id + ': ' + err);
    }
  });

  // 首次运行只记录基线，不触发
  var isFirstRun = Object.keys(snapshot).length === 0;
  props.setProperty(SNAPSHOT_KEY, JSON.stringify(current));

  if (!isFirstRun && changed.length) {
    Logger.log('检测到 ' + changed.length + ' 个文档更新，通知 GitHub…');
    dispatchGithub('docs:' + changed.join(','));
  }
}

/** 自检入口：手动运行，验证 PAT 与链路（日志出现 204 即成功） */
function testDispatch() {
  dispatchGithub('manual-test');
}

/** 向 GitHub 发送 repository_dispatch（Token 从脚本属性读取，不写入代码） */
function dispatchGithub(reason) {
  var props = PropertiesService.getScriptProperties();
  var pat = props.getProperty('GITHUB_PAT');
  if (!pat) {
    Logger.log('缺少 GITHUB_PAT 脚本属性，无法通知。请先在项目设置中配置。');
    return;
  }
  var last = Number(props.getProperty(LAST_DISPATCH_KEY) || 0);
  if (Date.now() - last < MIN_DISPATCH_GAP_MS) {
    Logger.log('60 秒内已派发过，跳过本次（reason=' + reason + '）。');
    return;
  }
  var resp = UrlFetchApp.fetch('https://api.github.com/repos/' + GITHUB_REPO + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + pat,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: JSON.stringify({ event_type: 'gdrive_update', client_payload: { reason: reason } }),
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  Logger.log('GitHub dispatch 响应码: ' + code + '（204 = 成功）' +
    (code === 204 ? '' : ' | ' + resp.getContentText()));
  if (code === 204) props.setProperty(LAST_DISPATCH_KEY, String(Date.now()));
}
