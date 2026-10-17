/**
 * 悬案录 · 前端交互
 * 判官守卷，侦探破局。前端只负责记录、排版与几处仪式感。
 */

const API = '/api';
const THEME_KEY = 'xuananlu-theme';

const state = {
    sessions: [],
    sessionId: null,
    caseFile: null,
    messages: [],
    resolved: false,
    loading: false,
    booting: false,
    bootLabel: '',
    theme: 'night',
};

const els = {
    html: document.documentElement,
    transcript: document.querySelector('#transcript'),
    input: document.querySelector('#chatInput'),
    composer: document.querySelector('#composer'),
    sendBtn: document.querySelector('#sendBtn'),
    newCaseBtn: document.querySelector('#newCaseBtn'),
    sessionList: document.querySelector('#sessionList'),
    sessionEmpty: document.querySelector('#sessionEmpty'),
    caseCount: document.querySelector('#caseCount'),
    caseNumber: document.querySelector('#caseNumber'),
    caseKicker: document.querySelector('#caseKicker'),
    caseTitle: document.querySelector('#caseTitle'),
    caseTags: document.querySelector('#caseTags'),
    watchState: document.querySelector('#watchState'),
    themeBtn: document.querySelector('#themeBtn'),
    themeMenu: document.querySelector('#themeMenu'),
    deck: document.querySelector('#deck'),
    resetChip: document.querySelector('#resetChip'),
    archives: document.querySelector('#archives'),
    railVeil: document.querySelector('#railVeil'),
    board: document.querySelector('#board'),
    boardVeil: document.querySelector('#boardVeil'),
    boardBody: document.querySelector('#boardBody'),
    boardPlaceholder: document.querySelector('#boardPlaceholder'),
    briefBlock: document.querySelector('#briefBlock'),
    sceneCard: document.querySelector('#sceneCard'),
    siteCard: document.querySelector('#siteCard'),
    sceneText: document.querySelector('#sceneText'),
    siteText: document.querySelector('#siteText'),
    suspectsBlock: document.querySelector('#suspectsBlock'),
    suspectList: document.querySelector('#suspectList'),
    suspectCount: document.querySelector('#suspectCount'),
    suspectHint: document.querySelector('#suspectHint'),
    clueBlock: document.querySelector('#clueBlock'),
    clueList: document.querySelector('#clueList'),
    clueHint: document.querySelector('#clueHint'),
    menuBtn: document.querySelector('#menuBtn'),
    panelBtn: document.querySelector('#panelBtn'),
    panelClose: document.querySelector('#panelClose'),
    overlay: document.querySelector('#overlay'),
    dialogKicker: document.querySelector('#dialogKicker'),
    dialogTitle: document.querySelector('#dialogTitle'),
    dialogText: document.querySelector('#dialogText'),
    confirmDialog: document.querySelector('#confirmDialog'),
    cancelDialog: document.querySelector('#cancelDialog'),
    toast: document.querySelector('#toast'),
};

let toastTimer = null;
let dialogAction = null;

/* ============================================================
   启动
   ============================================================ */

document.addEventListener('DOMContentLoaded', init);

async function init() {
    applyTheme(readTheme());
    bindEvents();

    state.booting = true;
    state.bootLabel = '判官正在翻检旧档，看看有没有还没结的案子';
    renderMessages();

    try {
        const sessions = await api('/sessions');
        state.booting = false;
        state.sessions = Array.isArray(sessions) ? sessions : [];
        renderArchive();

        if (state.sessions.length > 0) {
            await loadSession(state.sessions[0].session_id);
            if (!state.caseFile && state.messages.length === 0) await createNewCase();
        } else {
            await createNewCase();
        }
    } catch (error) {
        console.error('开局失败:', error);
        state.booting = false;
        renderMessages();
        toast(error.message || '连不上卷宗房，请确认服务已启动', 'error');
    }
}

/* ============================================================
   事件绑定
   ============================================================ */

function bindEvents() {
    els.composer.addEventListener('submit', (event) => {
        event.preventDefault();
        const text = els.input.value.trim();
        if (!text) return;
        els.input.value = '';
        sendCommand(text);
    });

    els.newCaseBtn.addEventListener('click', createNewCase);
    els.resetChip.addEventListener('click', askRestart);

    els.deck.addEventListener('click', (event) => {
        const chip = event.target.closest('.chip');
        if (!chip || chip === els.resetChip) return;

        const command = chip.dataset.cmd || '';
        if (!command) return;

        if (/[：:]$/.test(command)) {
            // 需要补全对象的指令先落进输入框
            els.input.value = command;
            els.input.focus();
            els.input.setSelectionRange(command.length, command.length);
        } else {
            sendCommand(command);
        }
    });

    els.transcript.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        if (button.dataset.action === 'retry') restartCase();
        else createNewCase();
    });

    els.sessionList.addEventListener('click', (event) => {
        const del = event.target.closest('[data-delete]');
        if (del) {
            askDelete(del.dataset.delete);
            return;
        }
        const open = event.target.closest('[data-session]');
        if (open) {
            closeDrawers();
            loadSession(open.dataset.session);
        }
    });

    els.suspectList.addEventListener('click', (event) => {
        const ask = event.target.closest('[data-suspect]');
        if (!ask) return;
        closeBoard();
        sendCommand(`盘问 ${ask.dataset.suspect}`);
    });

    els.clueList.addEventListener('click', (event) => {
        const chip = event.target.closest('[data-clue]');
        if (!chip) return;
        const clue = chip.dataset.clue;
        const suspects = (state.caseFile && state.caseFile.suspects) || [];
        const isPerson = suspects.some((person) => person.name === clue);
        closeBoard();
        sendCommand(`${isPerson ? '盘问' : '查验'} ${clue}`);
    });

    els.menuBtn.addEventListener('click', openArchives);
    els.railVeil.addEventListener('click', closeArchives);

    els.panelBtn.addEventListener('click', openBoard);
    els.panelClose.addEventListener('click', closeBoard);
    els.boardVeil.addEventListener('click', closeBoard);

    els.themeBtn.addEventListener('click', (event) => {
        event.stopPropagation();
        const open = els.themeMenu.classList.toggle('is-open');
        els.themeBtn.setAttribute('aria-expanded', String(open));
    });

    els.themeMenu.addEventListener('click', (event) => {
        event.stopPropagation();
        const item = event.target.closest('.theme-item');
        if (!item) return;
        applyTheme(item.dataset.theme);
        closeThemeMenu();
    });

    document.addEventListener('click', closeThemeMenu);

    els.cancelDialog.addEventListener('click', closeDialog);
    els.confirmDialog.addEventListener('click', () => {
        const action = dialogAction;
        closeDialog();
        if (action) action();
    });
    els.overlay.addEventListener('click', (event) => {
        if (event.target === els.overlay) closeDialog();
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (!els.overlay.hidden) {
            closeDialog();
        } else {
            closeThemeMenu();
            closeDrawers();
        }
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth > 1320) closeBoard();
        if (window.innerWidth > 960) closeArchives();
    });
}

/* ============================================================
   网络请求
   ============================================================ */

async function api(path, options = {}) {
    let response;
    try {
        response = await fetch(`${API}${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
    } catch (error) {
        throw new Error('连不上卷宗房，请确认服务已启动');
    }

    let body = null;
    try {
        body = await response.json();
    } catch (error) {
        body = null;
    }

    if (!response.ok || !body || body.code !== 200) {
        const message = (body && body.message) || `请求失败（${response.status}）`;
        throw new Error(message);
    }

    return body.data;
}

/* ============================================================
   主题
   ============================================================ */

function readTheme() {
    try {
        const saved = localStorage.getItem(THEME_KEY);
        if (saved === 'night' || saved === 'sepia') return saved;
    } catch (error) { /* 忽略 */ }
    return els.html.getAttribute('data-theme') === 'sepia' ? 'sepia' : 'night';
}

function applyTheme(theme) {
    const next = theme === 'sepia' ? 'sepia' : 'night';
    state.theme = next;
    els.html.setAttribute('data-theme', next);

    try {
        localStorage.setItem(THEME_KEY, next);
    } catch (error) { /* 忽略 */ }

    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.setAttribute('content', next === 'sepia' ? '#d9cdb3' : '#05070a');

    els.themeMenu.querySelectorAll('.theme-item').forEach((item) => {
        item.classList.toggle('is-active', item.dataset.theme === next);
    });
}

function closeThemeMenu() {
    if (!els.themeMenu.classList.contains('is-open')) return;
    els.themeMenu.classList.remove('is-open');
    els.themeBtn.setAttribute('aria-expanded', 'false');
}

/* ============================================================
   抽屉
   ============================================================ */

function openArchives() {
    closeBoard();
    els.archives.classList.add('is-open');
    els.railVeil.classList.add('is-visible');
}

function closeArchives() {
    els.archives.classList.remove('is-open');
    els.railVeil.classList.remove('is-visible');
}

function openBoard() {
    closeArchives();
    els.board.classList.add('is-open');
    els.boardVeil.classList.add('is-visible');
}

function closeBoard() {
    els.board.classList.remove('is-open');
    els.boardVeil.classList.remove('is-visible');
}

function closeDrawers() {
    closeArchives();
    closeBoard();
}

/* ============================================================
   卷宗库
   ============================================================ */

async function refreshArchive() {
    try {
        const sessions = await api('/sessions');
        state.sessions = Array.isArray(sessions) ? sessions : [];
        renderArchive();
    } catch (error) {
        console.error('卷宗库读取失败:', error);
    }
}

function renderArchive() {
    const sessions = state.sessions || [];
    els.caseCount.textContent = String(sessions.length).padStart(2, '0');
    els.sessionEmpty.hidden = sessions.length > 0;
    els.sessionList.innerHTML = '';

    sessions.forEach((item) => {
        const info = parseSessionMeta(item.session_id);
        const row = document.createElement('div');
        row.className = 'archive-item';
        if (item.session_id === state.sessionId) row.classList.add('is-active');

        const title = item.title ? `《${item.title}》` : '未命名卷宗';
        const meta = [item.era_label || '', item.type || ''].filter(Boolean).join(' · ') || '卷宗待阅';
        const time = formatTime(item.updated_at);

        row.innerHTML = `
            <button class="archive-open" type="button" data-session="${escapeHtml(item.session_id)}">
                <span class="archive-no">${escapeHtml(info.short)}</span>
                <span class="archive-main">
                    <span class="archive-title">${escapeHtml(title)}</span>
                    <span class="archive-meta">${escapeHtml(meta)}</span>
                    <span class="archive-meta">${escapeHtml(time)} · ${Number(item.turns) || 0} 卷笔录</span>
                </span>
                ${item.resolved ? '<span class="archive-solved">结</span>' : ''}
            </button>
            <button class="archive-del" type="button" data-delete="${escapeHtml(item.session_id)}" title="销毁此卷" aria-label="销毁此卷">✕</button>
        `;
        els.sessionList.appendChild(row);
    });
}

function parseSessionMeta(sessionId) {
    const matched = /^(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[_ :](\d{2})[_ :](\d{2})/.exec(sessionId || '');
    if (!matched) {
        return { short: '———', no: '——' };
    }
    const [, , month, day, hour, minute] = matched;
    return {
        short: `${month}${day}`,
        no: `${month}${day}·${hour}${minute}`,
    };
}

function formatTime(value) {
    if (!value) return '未知时间';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value).replace('T', ' ').slice(0, 16);
    }
    const pad = (number) => String(number).padStart(2, '0');
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/* ============================================================
   加载 / 开局 / 换新案
   ============================================================ */

async function loadSession(sessionId) {
    if (state.loading) return;

    state.booting = true;
    state.bootLabel = '判官正在从旧档里抽出这卷宗';
    renderMessages();

    try {
        const data = await api(`/sessions/${encodeURIComponent(sessionId)}`);
        state.sessionId = data.session_id || sessionId;
        state.caseFile = data.case || null;
        state.resolved = Boolean(data.resolved);
        state.messages = (data.messages || []).filter((message) => message && !message.hidden);
        state.booting = false;

        updateCaseUI();
        renderMessages();
        renderBoard();
        renderArchive();
        scrollToTop();
    } catch (error) {
        state.booting = false;
        renderMessages();
        toast(error.message || '卷宗读取失败', 'error');
    }
}

async function createNewCase() {
    if (state.loading || state.booting) return;

    state.booting = true;
    state.bootLabel = '判官正在旧档里翻找一桩还没结的命案';
    closeDrawers();
    renderMessages();
    setBusy(true);
    toast('判官正在调取一桩尚未结案的命案……');

    try {
        const data = await api('/sessions', { method: 'POST' });
        state.sessionId = data.session_id;
        state.caseFile = data.case || null;
        state.messages = data.opening ? [{ role: 'assistant', content: data.opening }] : [];
        state.resolved = false;
        state.booting = false;

        updateCaseUI();
        renderMessages();
        renderBoard();
        scrollToTop();
        await refreshArchive();

        if (data.notice) toast(data.notice, 'error');
        else if (!data.opening) toast('判官没有开局，可直接输入指令让他重开一桩', 'error');
    } catch (error) {
        state.booting = false;
        renderMessages();
        toast(error.message || '卷宗调取失败，请稍后再试', 'error');
    } finally {
        setBusy(false);
        els.input.focus();
    }
}

async function restartCase() {
    if (!state.sessionId) {
        await createNewCase();
        return;
    }
    if (state.loading || state.booting) return;

    state.booting = true;
    state.bootLabel = '旧卷正在封存，判官另抽年代与诡计';
    closeDrawers();
    renderMessages();
    setBusy(true);

    try {
        const data = await api(`/sessions/${encodeURIComponent(state.sessionId)}/restart`, { method: 'POST' });
        state.booting = false;

        if (!data.opening) {
            state.caseFile = data.case || null;
            state.messages = [];
            state.resolved = false;
            updateCaseUI();
            renderMessages();
            renderBoard();
            await refreshArchive();
            toast(data.notice || '卷宗调取失败，请稍后重试', 'error');
            return;
        }

        state.caseFile = data.case || null;
        state.messages = [{ role: 'assistant', content: data.opening }];
        state.resolved = false;

        updateCaseUI();
        renderMessages();
        renderBoard();
        scrollToTop();
        await refreshArchive();

        if (data.notice) toast(data.notice, 'error');
        else toast('旧卷已封存，新案另开。');
    } catch (error) {
        state.booting = false;
        renderMessages();
        toast(error.message || '换案失败，请稍后再试', 'error');
    } finally {
        setBusy(false);
        els.input.focus();
    }
}

async function deleteSession(sessionId) {
    try {
        await api(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });

        if (sessionId === state.sessionId) {
            state.sessionId = null;
            state.caseFile = null;
            state.messages = [];
            state.resolved = false;
            updateCaseUI();
            renderMessages();
            renderBoard();
        }

        await refreshArchive();
        toast('卷宗已销毁');

        if (!state.sessionId) {
            if (state.sessions.length > 0) await loadSession(state.sessions[0].session_id);
            else await createNewCase();
        }
    } catch (error) {
        toast(error.message || '销毁失败', 'error');
    }
}

/* ============================================================
   案卷头
   ============================================================ */

function updateCaseUI() {
    const meta = state.caseFile;
    const info = state.sessionId ? parseSessionMeta(state.sessionId) : null;

    els.caseNumber.textContent = info ? `NO. ${info.no}` : 'NO. ——';
    state.resolved = Boolean(state.resolved);

    els.watchState.classList.toggle('is-closed', state.resolved);
    const watchText = els.watchState.querySelector('span');
    if (watchText) watchText.textContent = state.resolved ? '此案已结' : '判官在卷';

    if (meta && (meta.title || meta.type || meta.era_label)) {
        const eraText = eraLabelOf(meta);
        els.caseTitle.textContent = meta.title ? `《${meta.title}》` : (meta.type || '未命名卷宗');
        els.caseKicker.textContent = [eraText, meta.year, meta.type].filter(Boolean).join(' · ') || '卷宗已调取';
        els.html.setAttribute('data-era', meta.era === 'modern' ? 'modern' : 'ancient');

        const tags = [];
        if (eraText) tags.push(`<span class="tag tag--era">${escapeHtml(eraText)}</span>`);
        if (meta.year) tags.push(`<span class="tag">${escapeHtml(meta.year)}</span>`);
        if (meta.type) tags.push(`<span class="tag tag--type">${escapeHtml(meta.type)}</span>`);
        if (state.resolved) tags.push('<span class="tag tag--solved">已结案</span>');
        els.caseTags.innerHTML = tags.join('');

        document.title = `${meta.title || meta.type || '悬案'} · 悬案录`;
    } else {
        els.caseTitle.textContent = state.sessionId ? '未命名卷宗' : '未启卷宗';
        els.caseKicker.textContent = state.sessionId ? '卷宗待判官补全' : '等待判官调卷';
        els.html.setAttribute('data-era', 'none');
        els.caseTags.innerHTML = '<span class="tag tag--ghost">待调阅</span>';
        document.title = '悬案录 · 文字探案';
    }
}

function eraLabelOf(meta) {
    if (!meta) return '';
    if (meta.era_label) return meta.era_label;
    if (meta.era === 'ancient') return '大靖';
    if (meta.era === 'modern') return '现代';
    return '';
}

/* ============================================================
   案卷板
   ============================================================ */

function renderBoard() {
    const meta = state.caseFile;

    if (!meta) {
        els.boardPlaceholder.hidden = false;
        els.briefBlock.hidden = true;
        els.suspectsBlock.hidden = true;
        els.clueBlock.hidden = true;
        return;
    }

    els.boardPlaceholder.hidden = true;

    const scene = (meta.scene || '').trim();
    const site = (meta.site || '').trim();
    els.briefBlock.hidden = !scene && !site;
    els.sceneCard.hidden = !scene;
    els.siteCard.hidden = !site;
    els.sceneText.textContent = scene;
    els.siteText.textContent = site;

    const suspects = Array.isArray(meta.suspects) ? meta.suspects : [];
    els.suspectsBlock.hidden = false;
    els.suspectList.innerHTML = suspects.map((person, index) => {
        const no = String(person.index || index + 1).padStart(2, '0');
        const role = [person.identity, person.relation].filter(Boolean).join(' · ') || '身份未记';
        return `
            <article class="suspect-card">
                <div class="suspect-top">
                    <span class="suspect-card__name">${escapeHtml(person.name)}</span>
                    <span class="suspect-card__no">${escapeHtml(no)}</span>
                </div>
                <p class="suspect-card__role">${escapeHtml(role)}</p>
                ${person.motive ? `<p class="suspect-card__motive">${escapeHtml(person.motive)}</p>` : ''}
                <button class="suspect-card__ask" type="button" data-suspect="${escapeHtml(person.name)}">盘问</button>
            </article>
        `;
    }).join('');
    els.suspectCount.textContent = String(suspects.length || 0).padStart(2, '0');
    els.suspectHint.hidden = suspects.length > 0;

    const clues = Array.isArray(meta.clues) ? meta.clues : [];
    els.clueBlock.hidden = false;
    els.clueList.innerHTML = clues.map((clue) => (
        `<button class="clue-chip" type="button" data-clue="${escapeHtml(clue)}">${escapeHtml(clue)}</button>`
    )).join('');
    els.clueHint.hidden = clues.length > 0;
}

/* ============================================================
   笔录渲染
   ============================================================ */

function renderMessages() {
    els.transcript.innerHTML = '';

    if (state.booting) {
        els.transcript.innerHTML = `
            <div class="transcript-empty is-loading">
                <div class="loading-seal">悬</div>
                <p class="empty-kicker">RETRIEVING THE FILE</p>
                <h3>正在调卷</h3>
                <p>${escapeHtml(state.bootLabel || '判官正在旧档里翻找卷宗')}，请稍候。</p>
                <span class="loading-bar"><i></i></span>
            </div>
        `;
        return;
    }

    if (state.messages.length === 0) {
        const hasSession = Boolean(state.sessionId);
        els.transcript.innerHTML = `
            <div class="transcript-empty">
                <div class="empty-seal">悬</div>
                <p class="empty-kicker">CASE FILE ${hasSession ? 'SEALED' : 'UNOPENED'}</p>
                <h3>${hasSession ? '卷宗未启' : '卷宗未启'}</h3>
                <p>${hasSession
                    ? '这一卷还没调取成功。判官就在案发现场等你，点下面按钮再试一次。'
                    : '判官在旧档里等你开口。点「开启新案」，他会从一桩还没结的命案说起。'}</p>
                <button class="btn-primary" type="button" data-action="${hasSession ? 'retry' : 'new'}">${hasSession ? '重试调卷' : '开启新案'}</button>
            </div>
        `;
        return;
    }

    state.messages.forEach((message) => {
        const element = buildMessage(message.role, message.content);
        if (element) els.transcript.appendChild(element);
    });
}

function buildMessage(role, content) {
    if (role === 'user' && isResetCommand(content)) {
        const divider = document.createElement('div');
        divider.className = 'case-break';
        divider.innerHTML = '<span>旧卷封存 · 另启新案</span>';
        return divider;
    }

    return role === 'user' ? buildPlayerMessage(content) : buildJudgeMessage(content);
}

function buildPlayerMessage(content) {
    const wrapper = document.createElement('div');
    wrapper.className = 'msg msg--player';

    const body = document.createElement('div');
    body.className = 'body';

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = '侦探';

    const text = document.createElement('div');
    text.className = 'text';
    text.textContent = content || '';

    body.append(meta, text);
    wrapper.appendChild(body);
    return wrapper;
}

function buildJudgeMessage(content) {
    const raw = String(content || '');
    const solved = raw.includes('【真凶】');

    const wrapper = document.createElement('div');
    wrapper.className = 'msg msg--judge';
    if (solved) wrapper.classList.add('is-verdict');

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = '判';

    const body = document.createElement('div');
    body.className = 'body';

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.innerHTML = '<span>判官</span><i></i><span>卷宗笔录</span>';

    const contentBox = document.createElement('div');
    contentBox.className = 'text';
    contentBox.innerHTML = formatContent(raw);

    body.append(meta, contentBox);

    if (solved) {
        const stamp = document.createElement('div');
        stamp.className = 'verdict-stamp';
        stamp.textContent = '结案';
        body.appendChild(stamp);
    }

    wrapper.append(avatar, body);
    return wrapper;
}

/* ============================================================
   判官文本排版
   ============================================================ */

function formatContent(raw) {
    const escaped = escapeHtml(raw || '').replace(/\r\n?/g, '\n');
    const lines = escaped.split('\n');
    const output = [];
    let paragraph = [];

    const flush = () => {
        if (paragraph.length === 0) return;
        output.push(`<p>${paragraph.join('<br>')}</p>`);
        paragraph = [];
    };

    lines.forEach((line) => {
        const trimmed = line.trim();
        if (!trimmed) {
            flush();
            return;
        }

        // 卷宗标题行：—— 时代 · 年份 · 类型 · 《案名》 ——
        if (/^[—–]{2,}.+[—–]{2,}$/.test(trimmed)) {
            flush();
            output.push(`<div class="case-title-line">${trimmed}</div>`);
            return;
        }

        // 小节标题：【案情】【现场】【嫌疑人】【可查】【真凶】……
        const heading = trimmed.match(/^【([^】]{1,12})】\s*(.*)$/);
        if (heading) {
            flush();
            const rest = heading[2].replace(/^[：:]\s*/, '');
            output.push(`<div class="case-heading">【${heading[1]}】</div>`);
            if (rest) output.push(`<p>${rest}</p>`);
            return;
        }

        // 嫌疑人条目：1. 姓名｜身份｜关系｜动机
        const suspect = trimmed.match(/^(\d{1,2})[.、．]\s*([^｜|]{1,24})[｜|](.+)$/);
        if (suspect) {
            flush();
            output.push(renderSuspect(suspect));
            return;
        }

        // 破折号列表
        if (/^[·•\-–—]\s+/.test(trimmed)) {
            flush();
            output.push(`<div class="bullet-line">${trimmed.replace(/^[·•\-–—]\s+/, '')}</div>`);
            return;
        }

        paragraph.push(trimmed);
    });

    flush();
    return output.join('') || '<p></p>';
}

function renderSuspect(matched) {
    const index = String(matched[1]).padStart(2, '0');
    const name = matched[2].trim();
    const description = matched[3]
        .split(/[｜|]/)
        .map((part) => part.trim())
        .filter(Boolean)
        .join(' · ');

    return `
        <div class="suspect-line">
            <span class="suspect-idx">${index}</span>
            <span class="suspect-name">${name}</span>
            <span class="suspect-desc">${description}</span>
        </div>
    `;
}

/* ============================================================
   对话
   ============================================================ */

async function sendCommand(raw) {
    const text = String(raw || '').trim();
    if (!text || state.loading) return;

    if (!state.sessionId) {
        toast('还没有开案。先点「开启新案」。', 'error');
        return;
    }

    const isReset = isResetCommand(text);
    state.loading = true;
    setBusy(true);

    if (!isReset) {
        state.messages.push({ role: 'user', content: text });
    }

    renderMessages();
    showPending();
    scrollBottom();

    try {
        const data = await api('/chat', {
            method: 'POST',
            body: JSON.stringify({ session_id: state.sessionId, message: text }),
        });

        hidePending();

        if (data.reset) {
            state.messages = data.reply ? [{ role: 'assistant', content: data.reply }] : [];
            state.caseFile = data.case || null;
            state.resolved = false;
        } else {
            if (data.reply) state.messages.push({ role: 'assistant', content: data.reply });
            if (data.case) state.caseFile = data.case;
            state.resolved = Boolean(data.resolved);
        }

        updateCaseUI();
        renderMessages();
        renderBoard();
        scrollBottom();
        await refreshArchive();

        if (data.notice) toast(data.notice, 'error');
    } catch (error) {
        hidePending();

        // 换案失败时以后台存档为准：可能旧案已封存，也可能请求根本没送达
        if (isReset) {
            state.loading = false;
            await loadSession(state.sessionId);
            await refreshArchive();
            toast(error.message || '换案失败，请稍后再试', 'error');
            return;
        }

        // 消息没有送达，撤回气泡并把指令还给侦探
        if (state.messages.length > 0 && state.messages[state.messages.length - 1].role === 'user') {
            state.messages.pop();
            els.input.value = text;
        }

        renderMessages();
        toast(error.message || '判官没有回应，请再试一次', 'error');
    } finally {
        state.loading = false;
        setBusy(false);
        els.input.focus();
    }
}

function showPending() {
    hidePending();

    const wrapper = document.createElement('div');
    wrapper.className = 'msg msg--judge is-pending';
    wrapper.id = 'pendingMessage';
    wrapper.innerHTML = `
        <div class="avatar">判</div>
        <div class="body">
            <div class="meta"><span>判官</span><i></i><span>正在翻卷</span></div>
            <div class="text pending-text">
                <span class="pulse-dot"></span>
                判官正在翻阅卷宗
                <span class="dots"><i>.</i><i>.</i><i>.</i></span>
            </div>
        </div>
    `;
    els.transcript.appendChild(wrapper);
    scrollBottom();
}

function hidePending() {
    const pending = document.querySelector('#pendingMessage');
    if (pending) pending.remove();
}

function scrollBottom() {
    const jump = () => {
        els.transcript.scrollTop = els.transcript.scrollHeight;
    };
    jump();
    requestAnimationFrame(() => {
        jump();
        requestAnimationFrame(jump);
    });
    setTimeout(jump, 120);
}

function scrollToTop() {
    els.transcript.scrollTop = 0;
    requestAnimationFrame(() => {
        els.transcript.scrollTop = 0;
    });
}

function setBusy(busy) {
    els.sendBtn.disabled = busy;
    els.newCaseBtn.disabled = busy;
    els.input.disabled = busy;
}

/* ============================================================
   指令 / 对话框
   ============================================================ */

function isResetCommand(text) {
    return /^\s*(?:请|麻烦|帮我|给我|我要|我想|那就|要不|我们)?\s*(?:换新案|换新案件|换新案子|换个新案|换个新案子|换个案子|换一桩案子|换一个案子|换一个新案|换一个新案子|换一案|新案|新案件|开新案|重开|重开一局|重新开局|重新开案|再来一局)\s*(?:吧|啊|呀|呗|了|好吗|行吗|可以吗)?\s*[。.!！?？]*\s*$/.test(String(text || ''));
}

function askRestart() {
    if (!state.sessionId) {
        createNewCase();
        return;
    }
    if (state.loading || state.booting) return;

    openDialog({
        kicker: 'SEAL THE FILE',
        title: '封存当前卷宗？',
        text: '旧案的人名、物证、线索将全部作废，判官会另抽时代与诡计，在同一条卷宗线上另开一桩新案。',
        confirmText: '封卷另开',
        onConfirm: restartCase,
    });
}

function askDelete(sessionId) {
    const info = parseSessionMeta(sessionId);
    openDialog({
        kicker: 'DESTROY THE FILE',
        title: '销毁这卷宗？',
        text: `卷宗 ${info.short} 的全部笔录将被永久删除，无法复原。`,
        confirmText: '销毁',
        onConfirm: () => deleteSession(sessionId),
    });
}

function openDialog(options) {
    els.dialogKicker.textContent = options.kicker || 'CONFIRM';
    els.dialogTitle.textContent = options.title || '确认操作';
    els.dialogText.textContent = options.text || '';
    els.confirmDialog.textContent = options.confirmText || '确认';
    dialogAction = options.onConfirm || null;
    els.overlay.hidden = false;
    requestAnimationFrame(() => els.confirmDialog.focus());
}

function closeDialog() {
    els.overlay.hidden = true;
    dialogAction = null;
}

/* ============================================================
   小工具
   ============================================================ */

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function toast(message, type = 'info') {
    els.toast.textContent = message;
    els.toast.dataset.type = type;
    els.toast.classList.add('is-show');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.remove('is-show'), 3400);
}
