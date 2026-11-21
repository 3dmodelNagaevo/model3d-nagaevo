(function () {   // всё внутри функции: скрипт MAX объявляет свои глобальные имена, не пересекаемся с ними
"use strict";
const C = window.SITE_CONFIG || {};
const API = (C.reviewApi || "").replace(/review=1/, "app=1");
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const thumb = p => p && p.startsWith("photos/") ? p.replace("photos/", "photos/t/") : p;
const rub = n => n == null ? "—" : n.toLocaleString("ru-RU") + " ₽";
const day = ts => new Date(ts).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
const WA = window.WebApp;
let INIT = WA && WA.initData || "";
let D = null;

function toast(t){ const el = $("#toast"); el.textContent = t; el.classList.add("on"); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("on"), 3200); }
function busy(on){ $("#bar").style.width = on ? "70%" : "100%"; if (!on) setTimeout(() => $("#bar").style.width = "0", 300); }

async function api(body){
  busy(true);
  try {
    if (!API) throw new Error("не задан адрес бота в config.js");
    const ctl = new AbortController(); const tm = setTimeout(() => ctl.abort(), 25000);
    const r = await fetch(API, { signal: ctl.signal, method: "POST", headers: { "Content-Type": "text/plain;charset=UTF-8" }, body: JSON.stringify({ initData: INIT, ...body }) });
    clearTimeout(tm);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(d.error || "Ошибка " + r.status), { code: r.status });
    return d;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("сервер долго не отвечает");
    if (e instanceof TypeError) throw new Error("нет связи с сервером бота (" + e.message + ")");
    throw e;
  } finally { busy(false); }
}
async function cmd(text, reload = true){
  try {
    const d = await api({ action: "cmd", text });
    toast((d.texts || []).join("\n\n").slice(0, 400) || "Готово");
    try { WA && WA.HapticFeedback && WA.HapticFeedback.notificationOccurred("success"); } catch(e){}
    if (reload) setTimeout(load, 600);
    return d;
  } catch (e) { toast("❌ " + e.message); }
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-cmd]");
  if (b) { e.preventDefault(); cmd(b.dataset.cmd); }
});

/* ---------- вкладки ---------- */
$$("nav button").forEach(b => b.onclick = () => {
  $$("nav button").forEach(x => x.classList.toggle("on", x === b));
  $$(".tab").forEach(t => t.classList.toggle("on", t.id === "t-" + b.dataset.t));
  window.scrollTo(0, 0);
});
const goTab = t => $(`nav button[data-t="${t}"]`).click();

/* ---------- данные ---------- */
const item = no => D.items.find(i => i.no === no);
const soldOf = no => D.sales.filter(s => s.no === no).reduce((a, s) => a + (+s.qty || 1), 0);
const priceOf = i => { const v = (D.overrides.price || {})[String(i.no)]; return v != null ? v : i.price; };

async function load(first){
  if (first) gate("Загружаю…", "Секунду, собираю данные магазина.");
  try {
    const d = await api({ action: "dash" });
    D = d.data;
    render();
  } catch (e) {
    if (e.code === 403) return location.replace("index.html");   // покупатель открыл приложение — показываем каталог
    if (e.code === 401) return gate("Откройте из бота", "Это приложение работает внутри MAX — откройте его кнопкой в чате с ботом.", true);
    if (!D) return gate("Не удалось загрузить", "Причина: " + (e.message || e) + (e.code ? " (код " + e.code + ")" : "") +
      ". Попробуйте ещё раз через минуту. Если повторяется — пришлите этот экран.", false, true);
    toast("❌ " + e.message);
  }
}
function gate(title, text, site, retry){
  $("#appMain").hidden = true; $("#nav").hidden = true;
  $("#gate").hidden = false;
  $("#gate").innerHTML = `<h1>${esc(title)}</h1><p class="muted">${esc(text)}</p>`
    + (retry ? `<p><button class="btn" onclick="__appLoad(true)">🔄 Повторить</button></p>` : "")
    + (site ? `<p><a class="btn" href="index.html">Перейти в каталог</a></p>` : "");
}

function render(){
  $("#appMain").hidden = false; $("#nav").hidden = false; $("#gate").hidden = true;
  $("#siteLink").href = D.site;
  renderHome(); renderItems(); renderSales(); renderPromos(); renderReviews(); renderMore();
  applyPerms(); renderMaintCard(); renderSched(); renderFin(); renderSite(); renderSpools(); renderNotify(); renderLog();
}

/* ---------- отложенные посты ---------- */
function renderSched(){
  const list = [...(D.scheduled || [])].sort((a, b) => a.at - b.at);
  $("#schedBox").hidden = !list.length || !can("posts");
  $("#schedList").innerHTML = list.map(p => `<div class="row" style="cursor:default"><div class="t"><b>${esc(p.title || "Пост")}</b>
    <small>${new Date(p.at * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}${(p.images || []).length ? " · фото: " + p.images.length : ""}</small></div>
    <button class="btn ghost sm" data-unsched="${esc(p.id)}">Отменить</button></div>`).join("");
}
$("#schedList").addEventListener("click", e => {
  const b = e.target.closest("[data-unsched]"); if (!b) return;
  confirmBox("Отменить отложенный пост?", "Пост не выйдет в канале.", "🗑 Да, отменить", "red", () => { closeSheet(); act({ action: "post_unschedule", id: b.dataset.unsched }); });
});

/* ---------- финансы ---------- */
let finF = "month";
$$("#finSeg button").forEach(b => b.onclick = () => { $$("#finSeg button").forEach(x => x.classList.toggle("on", x === b)); finF = b.dataset.f; renderFin(); });
const costOf = no => +((D.overrides.cost || {})[String(no)] || 0);
const salePrice = s => typeof s.p === "number" ? s.p : (item(s.no) && typeof priceOf(item(s.no)) === "number" ? priceOf(item(s.no)) : 0);
function finRange(f){
  const t = new Date();
  if (f === "month") return [new Date(t.getFullYear(), t.getMonth(), 1).getTime(), Infinity];
  if (f === "prev") return [new Date(t.getFullYear(), t.getMonth() - 1, 1).getTime(), new Date(t.getFullYear(), t.getMonth(), 1).getTime()];
  if (f === "year") return [new Date(t.getFullYear(), 0, 1).getTime(), Infinity];
  return [0, Infinity];
}
function finCalc(from, to){
  const ss = D.sales.filter(s => s.ts >= from && s.ts < to);
  const ex = (D.expenses || []).filter(x => x.ts >= from && x.ts < to);
  const rev = ss.reduce((a, s) => a + salePrice(s) * (+s.qty || 1), 0);
  const cost = ss.reduce((a, s) => a + costOf(s.no) * (+s.qty || 1), 0);
  const exp = ex.reduce((a, x) => a + (+x.amount || 0), 0);
  const orders = new Set(ss.map(s => s.b || s.ts)).size;
  return { rev, cost, exp, profit: rev - cost - exp, qty: ss.reduce((a, s) => a + (+s.qty || 1), 0), orders, avg: orders ? rev / orders : 0, ss, ex };
}
function renderFin(){
  if (!can("sales")) return;
  const [from, to] = finRange(finF);
  const f = finCalc(from, to);
  const r0 = n => Math.round(n).toLocaleString("ru-RU") + " ₽";
  $("#finKpi").innerHTML = `
    <div class="card kpi"><b>${r0(f.rev)}</b><span>выручка · ${f.qty} шт.</span></div>
    <div class="card kpi"><b style="color:${f.profit < 0 ? "#d33" : "var(--green)"}">${r0(f.profit)}</b><span>прибыль</span></div>
    <div class="card kpi"><b>${r0(f.exp)}</b><span>расходы</span></div>
    <div class="card kpi"><b>${r0(f.avg)}</b><span>средний чек · заказов ${f.orders}</span></div>
    ${f.cost ? `<div class="card kpi"><b>${r0(f.cost)}</b><span>себестоимость проданного</span></div>` : ""}`;
  // по месяцам (6 последних)
  const t = new Date(), months = [];
  for (let k = 5; k >= 0; k--) {
    const a = new Date(t.getFullYear(), t.getMonth() - k, 1), z = new Date(t.getFullYear(), t.getMonth() - k + 1, 1);
    const c = finCalc(a.getTime(), z.getTime());
    months.push({ label: a.toLocaleDateString("ru-RU", { month: "short" }).replace(".", ""), ...c });
  }
  const mx = Math.max(1, ...months.map(m => Math.max(m.rev, Math.abs(m.profit))));
  $("#finMonths").innerHTML = months.map(m => `<div class="mbar"><span>${esc(m.label)}</span>
      <div><i style="width:${Math.max(1, m.rev / mx * 100)}%" title="Выручка"></i><i class="${m.profit < 0 ? "neg" : ""}" style="width:${Math.max(1, Math.abs(m.profit) / mx * 100)}%;margin-top:3px;opacity:.55" title="Прибыль"></i></div>
      <b>${Math.round(m.rev).toLocaleString("ru-RU")} ₽<br><small class="muted">прибыль ${Math.round(m.profit).toLocaleString("ru-RU")}</small></b></div>`).join("")
    + `<p class="muted" style="font-size:12px;margin:6px 0 0">Тёмная полоса — выручка, светлая — прибыль (красная — убыток).</p>`;
  const byModel = {};
  f.ss.forEach(s => { const k = s.no; byModel[k] = byModel[k] || { rev: 0, qty: 0, profit: 0 }; const q = +s.qty || 1;
    byModel[k].rev += salePrice(s) * q; byModel[k].qty += q; byModel[k].profit += (salePrice(s) - costOf(s.no)) * q; });
  const top = Object.entries(byModel).sort((a, b) => b[1].rev - a[1].rev).slice(0, 7);
  $("#finTop").innerHTML = top.length ? top.map(([no, v]) => { const i = item(+no);
    return `<div class="mbar" style="grid-template-columns:1fr auto"><span>№${no} ${esc(i ? i.title : "")} · ${v.qty} шт.</span><b>${Math.round(v.rev).toLocaleString("ru-RU")} ₽</b></div>`; }).join("")
    : `<div class="muted">Продаж за период нет</div>`;
  const ex = [...(D.expenses || [])].sort((a, b) => b.ts - a.ts).slice(0, 60);
  $("#expenses").innerHTML = ex.map(x => `<div class="row" style="cursor:default"><div class="t"><b>${r0(x.amount)} · ${esc(x.cat)}</b><small>${day(x.ts)}${x.note ? " · " + esc(x.note) : ""}</small></div>
    <button class="btn ghost sm" data-exdel="${x.id}">🗑</button></div>`).join("") || `<div class="empty">Расходов пока нет</div>`;
}
$("#exDate").value = new Date().toISOString().slice(0, 10);
$("#exAdd").onclick = () => {
  const amount = parseFloat(($("#exAmt").value || "").replace(",", "."));
  if (!amount) return toast("Введите сумму");
  const ts = $("#exDate").value ? new Date($("#exDate").value + "T12:00").getTime() : Date.now();
  act({ action: "fin_add", amount, cat: $("#exCat").value, note: $("#exNote").value.trim(), ts }).then(() => { $("#exAmt").value = ""; $("#exNote").value = ""; });
};
$("#expenses").addEventListener("click", e => {
  const b = e.target.closest("[data-exdel]"); if (!b) return;
  confirmBox("Удалить расход?", "Запись исчезнет из финансов.", "🗑 Удалить", "red", () => { closeSheet(); act({ action: "fin_del", id: +b.dataset.exdel }); });
});
$("#exportBtn").onclick = () => act({ action: "export" }, false);

/* ---------- «Ещё»: разделы ---------- */
$$("#moreSeg button").forEach(b => b.onclick = () => {
  $$("#moreSeg button").forEach(x => x.classList.toggle("on", x === b));
  $$("[data-mp]").forEach(p => p.hidden = p.dataset.mp !== b.dataset.m);
});
function renderSite(){
  const n = D.notice || {};
  $("#ntOn").checked = !!n.on; $("#ntText").value = n.text || ""; $("#ntUntil").value = n.until || ""; $("#ntLink").value = n.link || "";
  const cols = D.collections || [];
  $("#colls").innerHTML = cols.map((c, k) => `<div class="row" data-coll="${k}"><div class="t"><b>${esc(c.title)}</b><small>${c.nos.length} мод.${c.on === false ? " · выключена" : ""}</small></div><div class="r">›</div></div>`).join("")
    || `<div class="empty">Подборок пока нет. Например: «Подарки к Новому году», «Для дома».</div>`;
  $$('#moreSeg [data-m="site"]').forEach(b => b.hidden = !can("site"));
  $$('#moreSeg [data-m="stock"]').forEach(b => b.hidden = !can("items"));
  $$('#moreSeg [data-m="log"]').forEach(b => b.hidden = !D.chief);
  $$('#moreSeg [data-m="term"]').forEach(b => b.hidden = !D.real);
  if ($("#termOut") && !$("#termOut").childNodes.length) tw("Терминал сайта. help — список команд, ls — файлы.", "m");
}
$("#ntSave").onclick = () => {
  const v = { on: $("#ntOn").checked, text: $("#ntText").value.trim(), until: $("#ntUntil").value, link: $("#ntLink").value.trim() };
  if (v.on && !v.text) return toast("Напишите текст объявления");
  act({ action: "set", key: "notice", value: v });
};
function saveColls(cols){ return act({ action: "set", key: "collections", value: cols }); }
function editColl(k){
  const cols = JSON.parse(JSON.stringify(D.collections || []));
  const c = k == null ? { id: "c" + Date.now(), title: "", nos: [], on: true } : cols[k];
  const sel = new Set(c.nos);
  sheet(`<div class="top"><h3>🗂 ${k == null ? "Новая подборка" : "Подборка"}</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form">
      <label>Название<input id="clTitle" value="${esc(c.title)}" placeholder="Подарки к Новому году"></label>
      <label class="chk"><input type="checkbox" id="clOn" ${c.on !== false ? "checked" : ""}> Показывать на сайте</label>
      <input id="clQ" type="search" placeholder="Поиск модели">
      <div class="list" id="clList" style="max-height:46vh;overflow:auto"></div>
      <button class="btn" id="clSave">Сохранить подборку</button>
      ${k != null ? `<button class="btn red" id="clDel">🗑 Удалить подборку</button>` : ""}
    </div>`);
  const draw = () => {
    const q = $("#clQ").value.trim().toLowerCase();
    $("#clList").innerHTML = [...D.items].filter(i => !q || (i.title + " " + i.no).toLowerCase().includes(q)).sort((a, b) => (sel.has(b.no) - sel.has(a.no)) || b.ts - a.ts)
      .map(i => `<label class="chk"><input type="checkbox" value="${i.no}" ${sel.has(i.no) ? "checked" : ""}> №${i.no} ${esc(i.title)}</label>`).join("");
  };
  draw();
  $("#clQ").oninput = draw;
  $("#clList").addEventListener("change", e => { const n = +e.target.value; e.target.checked ? sel.add(n) : sel.delete(n); });
  $("#clSave").onclick = () => {
    c.title = $("#clTitle").value.trim(); c.on = $("#clOn").checked; c.nos = [...sel];
    if (!c.title) return toast("Назовите подборку");
    if (!c.nos.length) return toast("Отметьте хотя бы одну модель");
    if (k == null) cols.push(c);
    closeSheet(); saveColls(cols);
  };
  if ($("#clDel")) $("#clDel").onclick = () => confirmBox("Удалить подборку?", "Она исчезнет с сайта.", "🗑 Удалить", "red", () => { cols.splice(k, 1); closeSheet(); saveColls(cols); });
}
$("#collAdd").onclick = () => editColl(null);
$("#colls").addEventListener("click", e => { const r = e.target.closest("[data-coll]"); if (r) editColl(+r.dataset.coll); });
$("#textsBtn").onclick = async () => {
  try {
    const html = await (await fetch("index.html?v=" + Date.now())).text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const fields = [...doc.querySelectorAll("[data-text]")].map(el => ({ key: el.dataset.text, label: el.dataset.label || el.dataset.text, list: !!el.dataset.list,
      def: el.dataset.list ? [...el.querySelectorAll("li")].map(li => li.textContent.trim()).join("\n") : el.textContent.trim().replace(/\s+/g, " ") }));
    const cur = D.texts || {};
    sheet(`<div class="top"><h3>✏️ Тексты сайта</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
      <p class="muted" style="font-size:13px">Меняйте текст и нажмите «Сохранить». Пустое поле — вернётся исходный текст. В списках каждый пункт — с новой строки.</p>
      <div class="form">${fields.map(f => `<label>${esc(f.label)}<textarea data-k="${esc(f.key)}" data-def="${esc(f.def)}" style="min-height:${f.list ? 110 : 80}px">${esc(cur[f.key] || f.def)}</textarea></label>`).join("")}
      <button class="btn" id="txSave">Сохранить тексты</button></div>`);
    $("#txSave").onclick = () => {
      const out = {};
      $$("#sheet textarea[data-k]").forEach(t => { const v = t.value.trim(); if (v && v !== t.dataset.def) out[t.dataset.k] = v; });
      closeSheet(); act({ action: "set", key: "texts", value: out });
    };
  } catch (e) { toast("❌ Не удалось загрузить тексты: " + e.message); }
};

/* ---------- пластик ---------- */
function renderSpools(){
  const sp = [...(D.spools || [])].sort((a, b) => (a.left / a.total) - (b.left / b.total));
  $("#spools").innerHTML = sp.map(x => { const low = x.left <= (x.low || 150);
    return `<div class="card"><div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(x.color)} · ${esc(x.material)}</b><b style="color:${low ? "#d33" : "inherit"}">${x.left} г</b></div>
      <div class="spbar"><u class="${low ? "low" : ""}" style="width:${Math.max(2, Math.round(x.left / x.total * 100))}%"></u></div>
      <div class="muted" style="font-size:13px;margin:4px 0 8px">из ${x.total} г${x.price ? " · " + rub(x.price) : ""}${low ? " · ⚠️ заканчивается" : ""}</div>
      <div class="btns"><button class="btn sm" data-spuse="${x.id}">➖ Потратил</button><button class="btn ghost sm" data-spset="${x.id}">✏️ Остаток</button><button class="btn ghost sm" data-spdel="${x.id}">🗑</button></div></div>`; }).join("")
    || `<div class="empty">Катушек пока нет — добавьте ниже.</div>`;
}
$("#spAdd").onclick = async () => {
  const color = $("#spColor").value.trim(); if (!color) return toast("Укажите цвет");
  const price = parseFloat($("#spPrice").value) || 0;
  await act({ action: "stock_add", color, material: $("#spMat").value.trim() || "PLA", grams: +$("#spG").value || 1000, price, low: +$("#spLow").value || 150 }, false);
  if ($("#spExp").checked && price && can("sales")) await act({ action: "fin_add", amount: price, cat: "Пластик", note: color + " " + ($("#spMat").value.trim() || "PLA") }, false);
  $("#spColor").value = ""; $("#spPrice").value = ""; setTimeout(load, 600);
};
$("#spools").addEventListener("click", e => {
  const u = e.target.closest("[data-spuse]"), st = e.target.closest("[data-spset]"), dl = e.target.closest("[data-spdel]");
  if (u || st) {
    const id = +(u || st).dataset[u ? "spuse" : "spset"];
    sheet(`<div class="top"><h3>${u ? "➖ Сколько потратили?" : "✏️ Сколько осталось?"}</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
      <div class="form"><label>Грамм<input id="spg" inputmode="numeric" placeholder="${u ? "120" : "640"}"></label><button class="btn" id="spOk">Сохранить</button></div>`);
    $("#spOk").onclick = () => { const g = +$("#spg").value; if (!g && g !== 0) return toast("Введите граммы"); closeSheet(); act({ action: u ? "stock_use" : "stock_set", id, grams: g }); };
  }
  if (dl) confirmBox("Убрать катушку?", "Она исчезнет из учёта пластика.", "🗑 Убрать", "red", () => { closeSheet(); act({ action: "stock_del", id: +dl.dataset.spdel }); });
});

/* ---------- уведомления ---------- */
const NOTIFY = [["orders", "🛒 Заказы и сообщения покупателей"], ["reviews", "💬 Новые отзывы с сайта"], ["promo", "🎟 Промокоды (выдача, окончание, автовыкладка)"],
  ["catalog", "🧩 Добавлено/убрано на сайте"], ["reports", "📅 Отчёт за неделю"], ["reminders", "📸 Напоминание о постах"], ["stock", "🧵 Заканчивается пластик"],
  ["posts", "📣 Отложенные посты вышли"], ["errors", "⚠️ Ошибки обновления сайта"], ["backup", "🗄 Еженедельная резервная копия"]];
function renderNotify(){
  const my = D.myNotify || {};
  $("#notifyBox").innerHTML = NOTIFY.map(([k, n]) => `<label class="chk"><input type="checkbox" data-nk="${k}" ${my[k] === false ? "" : "checked"}> ${n}</label>`).join("");
}
$("#notifySave").onclick = () => { const v = {}; $$("#notifyBox [data-nk]").forEach(c => v[c.dataset.nk] = c.checked); act({ action: "set", key: "notify", value: v }); };

/* ---------- журнал ---------- */
function renderLog(){
  const l = [...(D.log || [])].reverse().slice(0, 150);
  $("#logList").innerHTML = l.map(x => `<div class="row" style="cursor:default"><div class="t"><b>${esc(x.name)}</b>
    <small>${new Date(x.ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} · ${esc(x.what)}</small></div></div>`).join("")
    || `<div class="empty">Пока пусто — здесь появятся действия админов.</div>`;
}

/* ---------- права: скрываем разделы, на которые нет прав ---------- */
const can = p => (D.perms || []).includes(p);
function applyPerms(){
  const tabPerm = { items: "items", sales: "sales", promo: "promo", rev: "reviews", metr: "stats" };
  $$("nav button").forEach(b => { const p = tabPerm[b.dataset.t]; b.hidden = !!p && !can(p); });
  $("#nav").style.gridTemplateColumns = `repeat(${$$("nav button").filter(b => !b.hidden).length},1fr)`;
  $$("[data-perm]").forEach(el => el.hidden = !can(el.dataset.perm));
  $("#revAdd").hidden = !can("reviews");
}

/* ---------- фото: сжимаем на телефоне перед отправкой ---------- */
function shrink(file, max = 1280){
  return new Promise((ok, bad) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
      ok(c.toDataURL("image/jpeg", 0.82));
    };
    img.onerror = () => bad(new Error("не удалось прочитать фото"));
    img.src = url;
  });
}
async function pickPhotos(input, box, limit){
  const files = [...input.files].slice(0, limit);
  const out = [];
  for (const f of files) { try { out.push(await shrink(f)); } catch (e) { toast("❌ " + e.message); } }
  box.innerHTML = out.map(d => `<img src="${d}" alt="">`).join("");
  return out;
}
async function act(body, okReload = true){
  try { const d = await api(body); toast((d.texts || []).join("\n") || "Готово"); if (okReload) setTimeout(load, 800); return d; }
  catch (e) { toast("❌ " + e.message); }
}

/* ---------- новый пост ---------- */
$("#newPost").onclick = () => {
  let photos = [];
  sheet(`<div class="top"><h3>📸 Новый пост в канал</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form">
      <label>Текст поста<textarea id="npText" placeholder="Название, описание, размер, цвет, стоимость, #теги"></textarea></label>
      <button class="btn ghost sm" id="npTpl">📝 Вставить шаблон</button>
      <label>Фото (до 10)<input type="file" id="npPh" accept="image/*" multiple></label>
      <div class="thumbs" id="npThumbs"></div>
      <label>Опубликовать позже (необязательно)<input type="datetime-local" id="npAt"></label>
      <button class="btn" id="npGo">📣 Опубликовать в канал</button>
      <p class="muted" style="font-size:13px;margin:0">Если в тексте есть «Стоимость:», пост появится на сайте как товар.</p>
    </div>`);
  $("#npTpl").onclick = () => { $("#npText").value = D.template || ""; };
  $("#npPh").onchange = async e => { photos = await pickPhotos(e.target, $("#npThumbs"), 10); };
  $("#npGo").onclick = () => {
    const text = $("#npText").value.trim();
    if (!text && !photos.length) return toast("Добавьте текст или фото");
    const atv = $("#npAt").value;
    if (atv) {
      const at = Math.floor(new Date(atv).getTime() / 1000);
      if (!at || at < Date.now() / 1000 + 60) return toast("Время публикации должно быть в будущем");
      if (!text) return toast("Для отложенного поста нужен текст");
      return confirmBox("Запланировать пост?", "Пост выйдет в канале " + new Date(at * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) + " (может на 5–15 минут позже).",
        "🕒 Да, запланировать", "", () => { closeSheet(); toast("Сохраняю…"); act({ action: "post_schedule", text, photos, at }); });
    }
    confirmBox("Опубликовать пост?", "Пост сразу появится в канале — его увидят подписчики.", "📣 Да, опубликовать", "",
      () => { closeSheet(); toast("Публикую…"); act({ action: "post_new", text, photos }); });
  };
};
function confirmBox(title, text, yes, cls, then){
  sheet(`<div class="top"><h3>${title}</h3><button class="x" onclick="__appCloseSheet()">×</button></div><p>${text}</p>
    <div class="btns full"><button class="btn ${cls}" id="cbYes">${yes}</button><button class="btn ghost" id="cbNo">Отмена</button></div>`);
  $("#cbYes").onclick = then;
  $("#cbNo").onclick = closeSheet;
}

/* ---------- статус сайта ---------- */
$("#statusBtn").onclick = async () => {
  try { const d = await api({ action: "cmd", text: "/статус" });
    sheet(`<div class="top"><h3>📊 Статус сайта</h3><button class="x" onclick="__appCloseSheet()">×</button></div><pre class="metr">${esc((d.texts || []).join("\n\n"))}</pre>`);
  } catch (e) { toast("❌ " + e.message); }
};

/* ---------- технические работы ---------- */
function renderMaintCard(){
  const m = D.maint || {};
  $("#maintCard").hidden = !m.on;
  if (m.on) $("#maintCard").innerHTML = `<b>${m.mode === "closed" ? "🔴 Сайт закрыт для посетителей" : "🟡 На сайте висит баннер"}</b>
    <div class="muted">${esc(m.text || "")}${m.until ? " · до " + esc(m.until) : ""}</div>
    <div class="btns" style="margin-top:8px"><button class="btn sm" id="maintOff">🟢 Выключить тех. работы</button></div>`;
  if ($("#maintOff")) $("#maintOff").onclick = () => cmd("/техработы выкл");
}
$("#maintBtn").onclick = () => {
  const m = D.maint || {};
  sheet(`<div class="top"><h3>🚧 Технические работы</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form">
      <label class="chk"><input type="radio" name="mm" value="off" ${!m.on ? "checked" : ""}> 🟢 Сайт работает обычно</label>
      <label class="chk"><input type="radio" name="mm" value="banner" ${m.on && m.mode !== "closed" ? "checked" : ""}> 🟡 Баннер сверху (сайт работает)</label>
      <label class="chk"><input type="radio" name="mm" value="closed" ${m.on && m.mode === "closed" ? "checked" : ""}> 🔴 Закрыть сайт для посетителей</label>
      <label>Текст для посетителей<input id="mmText" value="${esc(m.text || "")}" placeholder="Например: обновляем каталог"></label>
      <label>Закончим к (необязательно)<input id="mmUntil" value="${esc(m.until || "")}" placeholder="18:00"></label>
      <button class="btn" id="mmSave">Сохранить</button>
      <p class="muted" style="font-size:13px;margin:0">Вы и админы видите сайт как обычно. Включается за 1–3 минуты.</p>
    </div>`);
  $("#mmSave").onclick = () => {
    const mode = (document.querySelector('input[name="mm"]:checked') || {}).value || "off";
    const until = $("#mmUntil").value.trim().replace(/[^\d:.]/g, "");
    const text = $("#mmText").value.trim();
    const c = mode === "off" ? "/техработы выкл" : `/техработы ${mode === "closed" ? "закрыть" : "баннер"}${until ? " до " + until : ""} ${text}`.trim() + (mode === "closed" ? " подтверждаю" : "");
    if (mode === "closed") return confirmBox("Закрыть сайт?", "Посетители увидят только экран «Технические работы» и кнопки связи.", "🔴 Да, закрыть", "red", () => { closeSheet(); cmd(c); });
    closeSheet(); cmd(c);
  };
};

/* ---------- добавить отзыв ---------- */
$("#revAdd").onclick = () => {
  let photos = [], stars = 5;
  sheet(`<div class="top"><h3>➕ Отзыв на сайт</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form">
      <label>Имя покупателя<input id="raName" placeholder="Например, Анна"></label>
      <label>Модель (необязательно)<select id="raNo"><option value="">— без модели —</option>${[...D.items].sort((a, b) => a.no - b.no).map(i => `<option value="${i.no}">№${i.no} ${esc(i.title)}</option>`).join("")}</select></label>
      <div><label>Оценка</label><div class="starpick" id="raStars">${[1,2,3,4,5].map(n => `<button type="button" data-n="${n}" class="on">★</button>`).join("")}</div></div>
      <label>Текст отзыва<textarea id="raText"></textarea></label>
      <label>Фото (до 4)<input type="file" id="raPh" accept="image/*" multiple></label>
      <div class="thumbs" id="raThumbs"></div>
      <button class="btn" id="raGo">Добавить на сайт</button>
    </div>`);
  $$("#raStars button").forEach(b => b.onclick = () => { stars = +b.dataset.n; $$("#raStars button").forEach(x => x.classList.toggle("on", +x.dataset.n <= stars)); });
  $("#raPh").onchange = async e => { photos = await pickPhotos(e.target, $("#raThumbs"), 4); };
  $("#raGo").onclick = () => {
    const text = $("#raText").value.trim(); if (text.length < 2) return toast("Напишите текст отзыва");
    closeSheet(); toast("Сохраняю…");
    act({ action: "review_add", text, name: $("#raName").value.trim(), no: $("#raNo").value, stars, photos });
  };
};

/* ---------- права админа ---------- */
function openPerms(a){
  const mine = new Set((D.adminPerms || {})[String(a.id)] || []);
  const names = D.permNames || {};
  sheet(`<div class="top"><h3>⚙️ Права: ${esc(a.name)}</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form">
      ${a.role === "full" ? `<div class="pill b">⭐ Сейчас полные права — всё как у владельца</div>` : ""}
      ${Object.entries(names).map(([k, n]) => `<label class="chk"><input type="checkbox" value="${k}" ${mine.has(k) ? "checked" : ""}> ${esc(n)}</label>`).join("")}
      <button class="btn" id="pmSave">Сохранить права</button>
      <div class="btns full">${D.real ? `<button class="btn ghost" id="pmFull">⭐ Полные права</button>` : ""}<button class="btn red" id="pmNone">🚫 Убрать все права</button></div>
    </div>`);
  $("#pmSave").onclick = () => {
    const sel = new Set($$('#sheet input[type=checkbox]:checked').map(i => i.value));
    const ops = Object.keys(names).map(k => (sel.has(k) ? "+" : "-") + k).join(" ");
    if (!sel.size) return toast("Отметьте хотя бы один раздел или нажмите «Убрать все права»");
    closeSheet(); cmd(`/права ${a.k} ${ops}`);
  };
  if ($("#pmFull")) $("#pmFull").onclick = () => confirmBox("Дать полные права?", `«${esc(a.name)}» сможет всё, как владелец, включая выдачу прав другим.`, "⭐ Да, дать", "", () => { closeSheet(); cmd(`/права ${a.k} полные`); });
  $("#pmNone").onclick = () => confirmBox("Убрать все права?", `«${esc(a.name)}» больше не сможет управлять ботом и открывать приложение.`, "🚫 Да, убрать", "red",
    () => confirmBox("Точно убрать?", "Это второе подтверждение.", "🚫 Да, точно", "red", () => { closeSheet(); cmd(`/права ${a.k} нет`); }));
}

/* ---------- сводка ---------- */
function renderHome(){
  const now = Date.now(), wk = 7 * 864e5;
  const sum = days => D.sales.filter(s => now - s.ts < days * 864e5)
    .reduce((a, s) => { const it = item(s.no); a.q += +s.qty || 1; a.r += ((it && priceOf(it)) || 0) * (+s.qty || 1); return a; }, { q: 0, r: 0 });
  const w = sum(7), m = sum(30);
  const visible = D.items.filter(i => !i.hidden).length;
  const act = D.promos.filter(p => p.status === "active").length;
  $("#kpis").innerHTML = `
    <div class="card kpi click" data-go="sales"><b>${w.q} шт.</b><span>продано за 7 дней · ≈ ${rub(w.r)}</span></div>
    <div class="card kpi click" data-go="sales"><b>${m.q} шт.</b><span>за 30 дней · ≈ ${rub(m.r)}</span></div>
    <div class="card kpi click" data-go="items"><b>${visible}</b><span>моделей на сайте${D.hiddenNos.length ? ` · скрыто ${D.hiddenNos.length}` : ""}</span></div>
    <div class="card kpi click" data-go="rev"><b>${D.pending.length}</b><span>отзывов ждут проверки</span></div>
    <div class="card kpi click" data-go="promo"><b>${act}</b><span>промокодов действует</span></div>
    <div class="card kpi"><b>${D.items.reduce((a, i) => a + (i.views_week || 0), 0)}</b><span>просмотров постов за неделю</span></div>`;
  $$("#kpis [data-go]").forEach(k => k.onclick = () => goTab(k.dataset.go));
  // продажи по неделям (8 недель)
  const weeks = [];
  for (let k = 7; k >= 0; k--) {
    const end = now - k * wk, start = end - wk;
    const q = D.sales.filter(s => s.ts >= start && s.ts < end).reduce((a, s) => a + (+s.qty || 1), 0);
    weeks.push({ q, label: day(start) });
  }
  const mx = Math.max(1, ...weeks.map(x => x.q));
  $("#weeks").innerHTML = weeks.map(x => `<div><em>${x.q || ""}</em><i style="height:${Math.round(x.q / mx * 80)}%"></i><small>${x.label}</small></div>`).join("");
  const top = [...D.items].filter(i => !i.hidden).sort((a, b) => (b.views_week || 0) - (a.views_week || 0)).slice(0, 5);
  $("#top").innerHTML = top.map(i => rowItem(i, `${i.views_week || 0} 👁`)).join("") || `<div class="empty">Пока нет данных</div>`;
  bindRows("#top");
}

/* ---------- товары ---------- */
let itemFilter = "all";
$$("#itemSeg button").forEach(b => b.onclick = () => { $$("#itemSeg button").forEach(x => x.classList.toggle("on", x === b)); itemFilter = b.dataset.f; renderItems(); });
$("#q").oninput = () => renderItems();
function rowItem(i, right){
  const pr = itemFilter === "check" ? problems(i) : [];
  return `<div class="row ${i.hidden ? "dim" : ""}" data-no="${i.no}">
    ${selMode ? `<input type="checkbox" class="sel" value="${i.no}" ${SEL.has(i.no) ? "checked" : ""} onclick="event.stopPropagation()">` : ""}
    <img src="${esc(thumb((i.photos || [])[0]) || "")}" alt="" loading="lazy">
    <div class="t"><b>${i.pin ? "📌 " : ""}${esc(i.title)}</b><small>№${i.no} · ${esc(i.category || "")}${i.hidden ? " · <span class='pill o'>скрыт</span>" : ""}${i.badge === "hit" ? " · 🔥 хит" : i.badge === "out" ? " · нет в наличии" : ""}</small>${pr.length ? `<span class="why">⚠️ ${pr.join(", ")}</span>` : ""}</div>
    <div class="r">${right != null ? right : rub(priceOf(i))}</div></div>`;
}
function bindRows(sel){ $$(sel + " .row[data-no]").forEach(r => r.onclick = () => { if (selMode && sel === "#items") { const c = r.querySelector(".sel"); c.checked = !c.checked; c.dispatchEvent(new Event("change", { bubbles: true })); return; } openItem(+r.dataset.no); }); }
function renderItems(){
  const q = $("#q").value.trim().toLowerCase();
  let list = D.items.filter(i => !q || (i.title + " " + i.no).toLowerCase().includes(q));
  if (itemFilter === "hidden") list = list.filter(i => i.hidden);
  if (itemFilter === "views") list.sort((a, b) => (b.views || 0) - (a.views || 0));
  else if (itemFilter === "sold") list = list.filter(i => soldOf(i.no)).sort((a, b) => soldOf(b.no) - soldOf(a.no));
  else if (itemFilter === "check") list = list.filter(i => problems(i).length);
  else list.sort((a, b) => (a.pin || 999) - (b.pin || 999) || (b.ts || 0) - (a.ts || 0));
  $("#items").innerHTML = list.map(i => rowItem(i, itemFilter === "views" ? `${i.views || 0} 👁` : itemFilter === "sold" ? `${soldOf(i.no)} шт.` : undefined)).join("")
    || `<div class="empty">${itemFilter === "check" ? "✅ Всё в порядке — у всех моделей есть фото, цена, описание и категория." : "Ничего не нашлось"}</div>`;
  bindRows("#items");
}
function problems(i){
  const p = [];
  if (!(i.photos || []).length) p.push("нет фото");
  if (typeof priceOf(i) !== "number") p.push("нет цены");
  if ((i.desc || "").trim().length < 20) p.push("короткое описание");
  if (!i.category || i.category === "Разное") p.push("категория «Разное»");
  if ((i.photos || []).length === 1) p.push("всего 1 фото");
  return p;
}
let selMode = false; const SEL = new Set();
$("#selMode").onclick = () => { selMode = !selMode; SEL.clear(); $("#selMode").textContent = selMode ? "✖️ Отменить выбор" : "☑️ Выбрать несколько"; updBulk(); renderItems(); };
function updBulk(){ $("#bulkBar").hidden = !selMode; $("#bulkCount").textContent = "Выбрано: " + SEL.size; }
$("#items").addEventListener("change", e => { if (!e.target.classList.contains("sel")) return; const n = +e.target.value; e.target.checked ? SEL.add(n) : SEL.delete(n); updBulk(); });
$$("[data-bulk]").forEach(b => b.onclick = () => {
  const op = b.dataset.bulk, nos = [...SEL];
  if (!nos.length) return toast("Отметьте товары галочками");
  const run = value => { closeSheet(); act({ action: "bulk", op, nos, value }).then(() => { SEL.clear(); updBulk(); }); };
  if (op === "pct") return sheet(`<div class="top"><h3>％ Изменить цены (${nos.length})</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form"><label>На сколько процентов (минус — снизить)<input id="bpct" inputmode="decimal" placeholder="10 или -15"></label>
    <p class="muted" style="font-size:13px;margin:0">Цены округляются до 10 ₽. Модели с ценой «по запросу» не меняются.</p><button class="btn" id="bpOk">Применить</button></div>`)
    || ($("#bpOk").onclick = () => { const v = parseFloat($("#bpct").value); if (!v) return toast("Введите процент"); confirmBox("Изменить цены?", `${nos.length} товар(ов): ${v > 0 ? "+" : ""}${v}%.`, "Да, изменить", "", () => run(v)); });
  if (op === "badge") return sheet(`<div class="top"><h3>🏷 Метка (${nos.length})</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="btns full"><button class="btn" data-bv="hit">🔥 Хит</button><button class="btn ghost" data-bv="out">Нет в наличии</button><button class="btn ghost" data-bv="">Убрать метку</button></div>`)
    || $$("#sheet [data-bv]").forEach(x => x.onclick = () => run(x.dataset.bv));
  const names = { hide: "Скрыть с сайта", show: "Вернуть на сайт", reset: "Вернуть цены из постов" };
  confirmBox(names[op] + "?", `Товаров: ${nos.length}.`, "Да", op === "hide" ? "red" : "", () => run(null));
});
$("#catPct").onclick = () => {
  const cats = [...new Set(D.items.map(i => i.category))].sort();
  sheet(`<div class="top"><h3>％ Цены по категории</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <div class="form"><label>Категория<select id="cpCat">${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select></label>
    <label>На сколько процентов (минус — снизить)<input id="cpPct" inputmode="decimal" placeholder="10"></label><button class="btn" id="cpOk">Применить</button></div>`);
  $("#cpOk").onclick = () => {
    const cat = $("#cpCat").value, v = parseFloat($("#cpPct").value);
    if (!v) return toast("Введите процент");
    const nos = D.items.filter(i => i.category === cat).map(i => i.no);
    confirmBox("Изменить цены?", `«${esc(cat)}»: ${nos.length} товар(ов), ${v > 0 ? "+" : ""}${v}%.`, "Да, изменить", "", () => { closeSheet(); act({ action: "bulk", op: "pct", nos, value: v }); });
  };
};
function sheet(html){ $("#sheet").innerHTML = html; $("#sheetBg").classList.add("on"); }
function closeSheet(){ $("#sheetBg").classList.remove("on"); }
let editorOpen = false;
$("#sheetBg").onclick = e => { if (e.target.id === "sheetBg" && !editorOpen) closeSheet(); };

function openItem(no){
  const i = item(no); if (!i) return;
  const ov = (D.overrides.price || {})[String(no)];
  const base = i.photos_orig || i.photos || [];
  const cover = (D.overrides.cover || {})[String(no)] || 1;
  sheet(`<div class="top"><h3>№${no} ${esc(i.title)}</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    ${base[0] ? `<img src="${esc(base[cover - 1] || base[0])}" alt="" style="width:100%;border-radius:16px;aspect-ratio:4/3;object-fit:cover">` : ""}
    <div class="grid2" style="margin:10px 0">
      <div class="card kpi"><b>${i.views || 0}</b><span>просмотров поста · ${i.views_week || 0} за неделю</span></div>
      <div class="card kpi"><b>${soldOf(no)} шт.</b><span>продано всего</span></div>
    </div>
    <div class="card form">
      <label>Цена на сайте, ₽ ${ov != null ? "<span class='pill b'>задана вручную</span>" : ""}<input id="iPrice" inputmode="numeric" value="${esc(priceOf(i))}"></label>
      <div class="btns full"><button class="btn" id="iPriceSave">💰 Сохранить цену</button>${ov != null ? `<button class="btn ghost" data-cmd="/цена ${no} сброс">↩️ Цена из поста</button>` : ""}</div>
    </div>
    <div class="card form" style="margin-top:10px">
      <b>Продажа</b>
      <div class="grid2"><label>Количество<input id="iQty" inputmode="numeric" value="1"></label>
        <div style="display:flex;align-items:flex-end"><button class="btn" id="iSold" style="width:100%">✅ Продано</button></div></div>
      ${soldOf(no) ? `<button class="btn ghost sm" data-cmd="/сбросить продажи ${no}">🧹 Обнулить «Продано»</button>` : ""}
    </div>
    ${can("items") ? `<h2>Карточка на сайте</h2>
    <div class="card form">
      <label>Название<input id="cTitle" value="${esc(i.title)}"></label>
      <label>Описание<textarea id="cDesc">${esc(i.desc || "")}</textarea></label>
      <label>Категория<input id="cCat" list="cats" value="${esc(i.category || "")}"><datalist id="cats">${[...new Set(D.items.map(x => x.category))].map(c => `<option value="${esc(c)}">`).join("")}</datalist></label>
      <label>Метка<select id="cBadge"><option value="">— нет —</option><option value="hit" ${i.badge === "hit" ? "selected" : ""}>🔥 Хит</option><option value="out" ${i.badge === "out" ? "selected" : ""}>Нет в наличии (под заказ)</option></select></label>
      <label class="chk"><input type="checkbox" id="cPin" ${i.pin ? "checked" : ""}> 📌 Закрепить вверху каталога</label>
      ${can("sales") ? `<label>Себестоимость 1 шт., ₽ (пластик + работа)<input id="cCost" inputmode="decimal" value="${esc((D.overrides.cost || {})[String(no)] || "")}" placeholder="например 60"></label>` : ""}
      <button class="btn" id="cSave">💾 Сохранить карточку</button>
      <p class="muted" style="font-size:13px;margin:0">Меняется только сайт, пост в канале остаётся прежним. Очистите поле — вернётся текст из поста.</p>
    </div>` : ""}
    ${base.length > 1 ? `<h2>Главное фото</h2><div class="covers">${base.slice(0, 10).map((p, k) =>
      `<button class="${k + 1 === cover ? "on" : ""}" data-cmd="/обложка ${no} ${k + 1}"><img src="${esc(thumb(p))}" alt="Фото ${k + 1}"></button>`).join("")}</div>` : ""}
    <h2>Ещё</h2>
    <div class="btns full">
      ${i.hidden ? `<button class="btn" data-cmd="/показать ${no}">👁 Вернуть на сайт</button>` : `<button class="btn red" data-cmd="/скрыть ${no}">🙈 Скрыть с сайта</button>`}
      <a class="btn ghost" href="${esc(D.site + "#m" + no)}" target="_blank" rel="noopener">🌐 На сайте</a>
      ${i.url ? `<a class="btn ghost" href="${esc(i.url)}" target="_blank" rel="noopener">📣 Пост</a>` : ""}
      ${can("posts") ? `<button class="btn ghost" id="iEdit">✏️ Изменить пост</button>` : ""}
    </div>`);
  if ($("#iEdit")) $("#iEdit").onclick = async () => {
    try {
      const d = await api({ action: "cmd", text: "/изменить " + no });
      const full = (d.texts || []).find(t => t.startsWith("/изменить " + no));
      if (!full) return toast((d.texts || []).join("\n") || "Не удалось получить текст поста");
      const cur = full.split("\n").slice(1).join("\n");
      sheet(`<div class="top"><h3>✏️ Пост №${no}</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
        <div class="form"><textarea id="peText" style="min-height:240px">${esc(cur)}</textarea>
        <button class="btn" id="peSave">Сохранить в канале</button>
        <p class="muted" style="font-size:13px;margin:0">Фото останутся прежними. Бот может менять только посты, которые сам опубликовал.</p></div>`);
      $("#peSave").onclick = () => { const t = $("#peText").value.trim(); if (!t) return toast("Пустой текст"); closeSheet(); cmd("/изменить " + no + "\n" + t); };
    } catch (e) { toast("❌ " + e.message); }
  };
  if ($("#cSave")) $("#cSave").onclick = () => {
    const ovr = D.overrides || {};
    const body = { action: "card", no, badge: $("#cBadge").value, pin: $("#cPin").checked };
    const t = $("#cTitle").value.trim(), d = $("#cDesc").value.trim(), c = $("#cCat").value.trim();
    body.title = t === ((i._orig || {}).title || i.title) && !(ovr.title || {})[String(no)] ? "" : t;
    body.desc = d === ((i._orig || {}).desc || i.desc || "") && !(ovr.desc || {})[String(no)] ? "" : d;
    body.cat = c === ((i._orig || {}).category || i.category) && !(ovr.cat || {})[String(no)] ? "" : c;
    if ($("#cCost")) body.cost = $("#cCost").value.trim();
    closeSheet(); act(body);
  };
  $("#iPriceSave").onclick = () => { const p = parseInt($("#iPrice").value, 10); if (!p) return toast("Введите цену числом"); cmd(`/цена ${no} ${p}`); };
  $("#iSold").onclick = () => { const q = parseInt($("#iQty").value, 10) || 1; cmd(`/продано ${no} ${q}`); };
  $$("#sheet [data-cmd]").forEach(b => b.addEventListener("click", () => setTimeout(closeSheet, 150)));
}

/* ---------- продажи ---------- */
$("#sAdd").onclick = () => { const no = parseInt($("#sNo").value, 10), q = parseInt($("#sQty").value, 10) || 1; if (!no) return toast("Введите номер товара"); cmd(`/продано ${no} ${q}`).then(() => { $("#sNo").value = ""; $("#sQty").value = 1; }); };
function renderSales(){
  const list = [...D.sales].sort((a, b) => b.ts - a.ts).slice(0, 150);
  $("#sales").innerHTML = list.map(s => { const i = item(s.no);
    return `<div class="row" style="cursor:default"><img src="${esc(thumb(i && (i.photos || [])[0]) || "")}" alt="">
      <div class="t"><b>${esc(i ? i.title : "№" + s.no)}</b><small>${day(s.ts)} · №${s.no} · ${s.qty || 1} шт.${s.id ? " · отметка #" + s.id : ""}</small></div>
      ${s.id ? `<button class="btn ghost sm" data-cmd="/удалить продажу ${s.id}">🗑</button>` : ""}</div>`; }).join("")
    || `<div class="empty">Продаж пока нет</div>`;
}

/* ---------- промокоды ---------- */
const disc = p => p.kind === "%" ? p.value + "%" : p.value + " ₽";
const ST = { active: ["действует", "g"], queued: ["в очереди", "b"], draft: ["черновик", ""], ended: ["закончился", "o"] };
function renderPromos(){
  const order = { active: 0, queued: 1, draft: 2, ended: 3 };
  const list = [...D.promos].sort((a, b) => order[a.status] - order[b.status] || (b.start || 0) - (a.start || 0));
  $("#promos").innerHTML = list.map(p => {
    const [st, cls] = ST[p.status] || [p.status, ""];
    const k = esc(p.code);
    const acts = p.status === "active"
      ? `<button class="btn sm" data-cmd="/промо списать ${k}">✅ Активировать</button><button class="btn ghost sm" data-cmd="/промо вернуть ${k}">↩️</button><button class="btn red sm" data-cmd="/промо стоп ${k}">⛔️ Стоп</button>`
      : p.status === "ended" ? ""
      : `<button class="btn sm" data-cmd="/промо опубликовать ${k}">📣 В канал</button>
         <button class="btn ghost sm" data-cmd="/промо ${p.status === "queued" ? "черновик" : "очередь"} ${k}">${p.status === "queued" ? "Из очереди" : "📅 В очередь"}</button>
         <button class="btn ghost sm" data-cmd="/промо тихо ${k}">🤫 Без поста</button>
         <button class="btn red sm" data-cmd="/промо удалить ${k}">🗑</button>`;
    return `<div class="card"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
      <b style="font-family:var(--display);font-size:16px">${k}</b><span class="pill ${cls}">${st}</span></div>
      <div class="muted" style="margin:4px 0 8px">скидка ${disc(p)} · активаций ${p.used || 0} из ${p.limit || "∞"}${p.until && p.status === "active" ? " · до " + day(p.until * 1000) : ""}${p.why ? " · " + esc(p.why) : ""}</div>
      ${acts ? `<div class="btns">${acts}</div>` : ""}</div>`; }).join("")
    || `<div class="empty">Промокодов пока нет</div>`;
  if (D.personal.length) $("#promos").insertAdjacentHTML("beforeend",
    `<div class="card muted">🎁 Личных кодов за подписку выдано: ${D.personal.length}, использовано: ${D.personal.filter(p => p.used).length}</div>`);
  const s = D.schedule || {}, days = ["пн","вт","ср","чт","пт","сб","вс"];
  $("#schedInfo").textContent = s.on ? `Сейчас: каждый ${days[s.wd]} в ${s.hm} — ${s.value}${s.kind === "%" ? "%" : " ₽"}, ${s.limit} акт., ${s.days} дн.` : "Сейчас выключена.";
  if (s.on) { $("#schDay").value = days[s.wd]; $("#schTime").value = s.hm; $("#schDisc").value = s.value + (s.kind === "%" ? "%" : "₽"); $("#schLim").value = s.limit; $("#schDays").value = s.days; }
  const sub = D.sub || {};
  $("#subInfo").textContent = sub.on ? `Сейчас: ${sub.value}${sub.kind === "%" ? "%" : " ₽"}, код на ${sub.days} дн.` : "Сейчас выключена.";
}
$("#pNew").onclick = () => {
  const code = ($("#pCode").value.trim() || "авто").replace(/\s+/g, ""), d = $("#pDisc").value.trim().replace(/\s+/g, "");
  if (!d) return toast("Укажите скидку: 10% или 100₽");
  cmd(`/промо новый ${code} ${d} ${parseInt($("#pLim").value, 10) || 0} ${parseInt($("#pDays").value, 10) || 7}`).then(() => { $("#pCode").value = ""; });
};
$("#schOn").onclick = () => cmd(`/промо расписание ${$("#schDay").value} ${$("#schTime").value.trim()} ${$("#schDisc").value.replace(/\s+/g, "")} ${parseInt($("#schLim").value, 10) || 3} ${parseInt($("#schDays").value, 10) || 7}`);
$("#subOn").onclick = () => cmd(`/промо подписка ${$("#subDisc").value.replace(/\s+/g, "")} ${parseInt($("#subDays").value, 10) || 14}`);
$("#chkBtn").onclick = () => { const c = $("#chk").value.trim(); if (!c) return toast("Введите промокод"); cmd(`/код ${c}`, false); };

/* ---------- отзывы ---------- */
const stars = n => n ? `<span class="stars">${"★".repeat(n)}${"☆".repeat(5 - n)}</span>` : "";
function renderReviews(){
  $("#revDot").hidden = !D.pending.length; $("#revDot").textContent = D.pending.length;
  $("#pend").innerHTML = D.pending.map(r => { const i = item(r.no);
    return `<div class="card rev"><b>${esc(r.name || "Покупатель")}</b> ${stars(r.stars)} <small class="muted">${day(r.ts)}${i ? " · №" + r.no + " " + esc(i.title) : ""}</small>
      ${r.why ? `<div><span class="pill o">${esc(r.why)}</span></div>` : ""}<p>${esc(r.text)}</p>
      <div class="btns"><button class="btn sm" data-cmd="/одобрить ${r.id}">✅ Опубликовать</button><button class="btn red sm" data-cmd="/отклонить ${r.id}">🗑 Удалить</button></div></div>`; }).join("")
    || `<div class="empty">Нет отзывов на проверке 👍</div>`;
  const pub = [...D.reviewsBot].sort((a, b) => b.ts - a.ts);
  $("#revs").innerHTML = pub.map(r => { const i = r.no && item(r.no);
    return `<div class="card rev"><b>${esc(r.name || "Отзыв")}</b> ${stars(r.stars)} <small class="muted">${day(r.ts)}${i ? " · №" + r.no + " " + esc(i.title) : ""}${r.auto ? " · авто" : ""}</small>
      <p>${esc(r.text)}</p><button class="btn ghost sm" data-cmd="/удалить отзыв ${r.id}">🗑 Убрать с сайта</button></div>`; }).join("")
    || `<div class="empty">Опубликованных отзывов пока нет</div>`;
}

/* ---------- ещё ---------- */
const ROLE = { owner: ["👑 владелец", "g"], full: ["⭐ полные права", "b"], on: ["✅ есть доступ", "g"], off: ["🚫 нет доступа", ""] };
function renderMore(){
  $("#admins").innerHTML = D.admins.map(a => { const [t, c] = ROLE[a.role];
    const btn = !D.chief || a.role === "owner" ? "" : a.role === "off"
      ? `<button class="btn sm" data-cmd="/разрешить ${a.k}">Дать доступ</button>`
      : `<button class="btn ghost sm" data-perms="${a.k}">⚙️ Права</button><button class="btn ghost sm" data-revoke="${a.k}" data-name="${esc(a.name)}" data-full="${a.role === "full" ? 1 : ""}">Забрать</button>`;
    const pc = a.role === "on" ? ((D.adminPerms || {})[String(a.id)] || []).length : 0;
    return `<div class="row" style="cursor:default;flex-wrap:wrap"><div class="t"><b>${esc(a.name)}</b><small>${a.guest ? "по приглашению · " : ""}<span class="pill ${c}">${t}</span>${pc ? ` · разделов: ${pc} из ${Object.keys(D.permNames || {}).length}` : ""}</small></div><div class="btns">${btn}</div></div>`; }).join("")
    || `<div class="empty">Список админов недоступен</div>`;
  $("#inviteBtn").hidden = !D.chief;
  renderLinks();
}
$("#admins").addEventListener("click", e => {      // забрать доступ — с подтверждением
  const pb = e.target.closest("[data-perms]");
  if (pb) return openPerms(D.admins.find(a => String(a.k) === pb.dataset.perms));
  const b = e.target.closest("[data-revoke]"); if (!b) return;
  const k = b.dataset.revoke, name = b.dataset.name;
  sheet(`<div class="top"><h3>Забрать доступ?</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <p>У «${esc(name)}» будет забран доступ${b.dataset.full ? " и полные права" : ""}. Он больше не сможет управлять ботом и открывать это приложение.</p>
    <div class="btns full"><button class="btn red" id="revYes">🚫 Да, забрать</button><button class="btn ghost" onclick="__appCloseSheet()">Отмена</button></div>`);
  $("#revYes").onclick = () => { closeSheet(); cmd(`/запретить ${k} да`); };
});
$("#inviteBtn").onclick = () => {
  const names = D.permNames || {};
  sheet(`<div class="top"><h3>➕ Пригласить по ссылке</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <p class="muted" style="font-size:13px">Что сможет делать новый человек (потом можно поменять через «⚙️ Права»):</p>
    <div class="form">
      ${Object.entries(names).filter(([k]) => k !== "access" || D.real).map(([k, n]) => `<label class="chk"><input type="checkbox" value="${k}" ${k !== "access" ? "checked" : ""}> ${esc(n)}</label>`).join("")}
      <button class="btn" id="invGo">🔗 Создать ссылку</button>
    </div>`);
  $("#invGo").onclick = async () => {
    const perms = $$('#sheet input[type=checkbox]:checked').map(i => i.value);
    if (!perms.length) return toast("Отметьте хотя бы один раздел");
    const d = await act({ action: "invite", perms }, false);
    if (!d || !d.link) return;
    sheet(`<div class="top"><h3>🔗 Ссылка готова</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
      <p>Перешлите её человеку, которому даёте доступ. Он откроет бота — и доступ появится сразу, а вам придёт уведомление.</p>
      <div class="form"><input id="invLink" readonly value="${esc(d.link)}">
      <button class="btn" id="invCopy">📋 Скопировать ссылку</button></div>
      <p class="muted" style="font-size:13px">Работает 24 часа и только один раз. Не выкладывайте её в канал и группы. Забрать доступ — кнопка «Забрать» в списке.</p>`);
    $("#invCopy").onclick = async () => {
      const inp = $("#invLink");
      try { await navigator.clipboard.writeText(inp.value); }
      catch (e) { inp.focus(); inp.select(); try { document.execCommand("copy"); } catch (e2) {} }
      toast("Ссылка скопирована");
    };
    setTimeout(load, 800);
  };
};
$("#lq").oninput = () => renderLinks();

/* ---------- договор ---------- */
$("#ctBtn").onclick = async () => {
  let info = { settings: {}, issued: [] };
  try { info = (await api({ action: "contract", op: "get" })).data; } catch (e) { return toast("❌ " + e.message); }
  const st = info.settings || {};
  const rows = [];
  const rowHtml = (k, r) => `<div class="card" style="padding:10px;margin:0" data-row="${k}">
      <div class="grid2"><label>${r.kind === "model" ? "№ модели" : "Услуга"}<input data-f="${r.kind === "model" ? "no" : "name"}" value="${esc(r.kind === "model" ? (r.no || "") : (r.name || ""))}" ${r.kind === "model" ? 'inputmode="numeric" placeholder="38"' : 'placeholder="Моделирование по фото"'}></label>
      <label>Кол-во<input data-f="qty" inputmode="decimal" value="${esc(r.qty || 1)}"></label></div>
      <div class="grid2"><label>Цена за шт., ₽${r.kind === "model" ? " (пусто — с сайта)" : ""}<input data-f="price" inputmode="decimal" value="${esc(r.price || "")}"></label>
      <button class="btn ghost sm" data-del="${k}" style="align-self:end">🗑 Убрать</button></div>
      <small class="muted" data-title></small></div>`;
  sheet(`<div class="top"><h3>📄 Договор</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <p class="muted" style="font-size:13px;margin-top:0">Бот пришлёт в чат PDF (переслать покупателю) и Word (если нужно поправить). Подписи не нужны: покупатель отвечает в чате «Ознакомлен, согласен» или ставит реакцию.</p>
    <div class="form">
      <label>Заказчик — имя аккаунта в MAX<input id="ctName" placeholder="как подписан в MAX, можно пусто"></label>
      <label>Телефон заказчика (необязательно)<input id="ctPhone" inputmode="tel" placeholder="можно оставить пустым"></label>
      <b>Что делаем</b>
      <div id="ctRows" style="display:flex;flex-direction:column;gap:8px"></div>
      <div class="btns"><button class="btn ghost sm" id="ctAddM">➕ Модель с сайта</button><button class="btn ghost sm" id="ctAddS">➕ Услуга</button></div>
      <div class="grid2"><label>Срок<input id="ctDays" value="${esc(st.days || "по договорённости")}"></label><label>Предоплата, %<input id="ctPre" inputmode="numeric" value="${esc(st.prepay ? st.prepay : "")}" placeholder="по договорённости"></label></div>
      <label>Получение<input id="ctDel" value="${esc(st.delivery || "по договорённости")}"></label>
      <div id="ctSum" class="muted"></div>
      <button class="btn" id="ctGo">📄 Сформировать договор</button>
      <details style="margin-top:6px"><summary><b>👤 Исполнитель в договоре</b></summary>
        <div class="form" style="margin-top:8px">
          <label>Исполнитель<input id="ctMeName" value="${esc(st.name || "3D модели Нагаево")}"></label>
          <label>Ваш телефон<input id="ctMePhone" value="${esc(st.phone || "")}" inputmode="tel"></label>
          <label>Населённый пункт<input id="ctMeCity" value="${esc(st.city || "с. Нагаево")}"></label>
          <button class="btn ghost" id="ctMeSave">💾 Сохранить мои данные</button>
        </div></details>
      ${(info.issued || []).length ? `<details><summary><b>📋 Выданные договоры</b></summary>${info.issued.slice().reverse().map(x => `<div class="muted" style="font-size:13px;margin:4px 0">№${x.n} от ${esc(x.date)} — ${esc(x.customer || "без имени")}, ${Math.round(x.total)} ₽</div>`).join("")}</details>` : ""}
    </div>`);
  const draw = () => {
    $("#ctRows").innerHTML = rows.map((r, k) => rowHtml(k, r)).join("") || `<div class="muted">Добавьте модель или услугу</div>`;
    upd();
  };
  const upd = () => {
    let sum = 0;
    $$("#ctRows [data-row]").forEach(el => {
      const r = rows[+el.dataset.row];
      el.querySelectorAll("[data-f]").forEach(i => r[i.dataset.f] = i.value.trim());
      let price = parseFloat(String(r.price).replace(",", "."));
      const t = el.querySelector("[data-title]");
      if (r.kind === "model") {
        const it = item(+r.no);
        t.textContent = r.no ? (it ? it.title + (typeof priceOf(it) === "number" ? " · " + rub(priceOf(it)) : " · цены нет — укажите") : "нет такой модели") : "";
        if (isNaN(price) && it && typeof priceOf(it) === "number") price = priceOf(it);
      }
      sum += (isNaN(price) ? 0 : price) * (parseFloat(String(r.qty).replace(",", ".")) || 1);
    });
    $("#ctSum").textContent = sum ? "Итого: " + Math.round(sum).toLocaleString("ru-RU") + " ₽" : "";
  };
  $("#ctRows").addEventListener("input", upd);
  $("#ctRows").addEventListener("click", e => { const b = e.target.closest("[data-del]"); if (b) { upd(); rows.splice(+b.dataset.del, 1); draw(); } });
  $("#ctAddM").onclick = () => { upd(); rows.push({ kind: "model", qty: 1 }); draw(); };
  $("#ctAddS").onclick = () => { upd(); rows.push({ kind: "service", qty: 1 }); draw(); };
  rows.push({ kind: "model", qty: 1 }); draw();
  $("#ctMeSave").onclick = () => act({ action: "contract", op: "settings", value: { name: $("#ctMeName").value, phone: $("#ctMePhone").value, city: $("#ctMeCity").value } }, false);
  $("#ctGo").onclick = async () => {
    upd();
    const items = rows.filter(r => r.kind === "model" ? r.no : r.name).map(r => r.kind === "model" ? { no: r.no, qty: r.qty, price: r.price } : { name: r.name, qty: r.qty, price: r.price });
    if (!items.length) return toast("Добавьте модель или услугу");
    if ($("#ctMeName").value.trim() && $("#ctMeName").value.trim() !== (st.name || "")) await act({ action: "contract", op: "settings", value: { name: $("#ctMeName").value, phone: $("#ctMePhone").value, city: $("#ctMeCity").value } }, false);
    $("#ctGo").disabled = true; $("#ctGo").textContent = "Готовлю…";
    try {
      const d = await api({ action: "contract", customer: { name: $("#ctName").value, phone: $("#ctPhone").value }, items,
        days: $("#ctDays").value, prepay: $("#ctPre").value, delivery: $("#ctDel").value });
      closeSheet(); toast((d.texts || []).join("\n"));
    } catch (e) { toast("❌ " + e.message); $("#ctGo").disabled = false; $("#ctGo").textContent = "📄 Сформировать договор"; }
  };
};

/* ---------- место на GitHub ---------- */
$("#spaceBtn").onclick = async () => {
  let d;
  try { d = await api({ action: "cmd", text: "/место" }); } catch (e) { return toast("❌ " + e.message); }
  sheet(`<div class="top"><h3>💾 Место на GitHub</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <pre class="metr" style="white-space:pre-wrap">${esc((d.texts || []).join("\n\n").split("\n\nЧто можно сделать:")[0])}</pre>
    <div class="btns full" style="flex-direction:column">
      <button class="btn ghost" id="spClean">🧹 Убрать лишние файлы</button>
      <button class="btn ghost" id="spRuns">🗑 Удалить старые запуски обновлений</button>
      ${D.real ? `<button class="btn red" id="spSquash">🗜 Сжать историю (освобождает больше всего)</button>` : ""}
    </div>
    <p class="muted" style="font-size:13px">Предел — около 1 ГБ. Бот сам предупредит, когда место начнёт заканчиваться.</p>`);
  const run = async (text, title) => { try { const r = await api({ action: "cmd", text }); confirmOnly(title, (r.texts || []).join("\n\n")); } catch (e) { toast("❌ " + e.message); } };
  const confirmOnly = (title, text) => sheet(`<div class="top"><h3>${title}</h3><button class="x" onclick="__appCloseSheet()">×</button></div><pre class="metr" style="white-space:pre-wrap">${esc(text)}</pre>`);
  $("#spClean").onclick = () => confirmBox("Убрать лишние файлы?", "Удалятся фото удалённых отзывов и другие файлы, которые нигде не используются.", "🧹 Да, убрать", "", () => run("/место очистить да", "🧹 Очистка"));
  $("#spRuns").onclick = () => confirmBox("Удалить старые запуски?", "Журналы старых обновлений сайта на GitHub. Последние 30 останутся.", "🗑 Да, удалить", "", () => run("/место запуски", "🗑 Старые запуски"));
  if ($("#spSquash")) $("#spSquash").onclick = () => confirmBox("Сначала резервная копия!", "Сжатие оставит на GitHub только текущую версию сайта. Откатить файлы к старым версиям станет нельзя. Сайт, бот, продажи и настройки не пострадают. Вся история сохранится только в резервной копии.",
    "Копия уже есть — дальше", "red", () => confirmBox("Точно сжать историю?", "Это нельзя отменить (только восстановлением из копии).", "🗜 Да, сжать", "red", () => run("/место история подтверждаю", "🗜 Сжатие истории")));
};

/* ---------- терминал (только владелец) ---------- */
const TERM_HELP = `Команды терминала:
  ls [папка]          — файлы (нажмите на файл — откроется редактор)
  find <слово>        — найти файлы по имени
  cat <файл>          — показать файл
  edit <файл>         — редактировать и сохранить
  new <файл>          — создать новый файл
  rm <файл>           — удалить файл (с подтверждением)
  history [файл]      — последние изменения (всего сайта или файла)
  restore <файл> <версия> — вернуть файл к прошлой версии
  runs                — обновления сайта на GitHub (успех/ошибка)
  sync                — обновить сайт прямо сейчас
  /команда            — любая команда бота: /статус, /цена 5 300, /промо …
  clear               — очистить экран

Что где лежит:
  index.html — сайт; config.js — телефон, ссылки; app.html/app.js — это приложение
  yandex/bot.py — бот; scripts/ — синхронизация с каналом
  *.json — данные: overrides (цены, скрытые), sales, promos, reviews, settings, access…
JSON и Python проверяются перед сохранением — сломать бота опечаткой не выйдет.
Любую правку можно откатить: history <файл> → restore.`;
let termHist = []; try { termHist = JSON.parse(localStorage.getItem("termHist") || "[]"); } catch (e) {}
let termPos = -1;
function tw(text, cls){ const o = $("#termOut"); const d = document.createElement("div"); if (cls) d.className = cls; d.textContent = text; o.appendChild(d); o.scrollTop = o.scrollHeight; return d; }
function twLink(text, run, cls){ const d = tw(text, "l" + (cls ? " " + cls : "")); d.onclick = () => termRun(run); return d; }
async function term(op, extra){
  try { const d = await api({ action: "term", op, ...extra }); return d.data; }
  catch (e) { tw("✖ " + e.message, "e"); return null; }
}
const kb = n => n == null ? "" : n > 1024 ? (n / 1024).toFixed(1) + " КБ" : n + " Б";
const dt = s => new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
async function termRun(line){
  line = (line || "").trim(); if (!line) return;
  tw("$ " + line, "c");
  if (termHist[0] !== line) { termHist.unshift(line); termHist = termHist.slice(0, 50); try { localStorage.setItem("termHist", JSON.stringify(termHist)); } catch (e) {} }
  termPos = -1;
  const [c, ...rest] = line.split(/\s+/); const arg = rest.join(" "); const cmdl = c.toLowerCase();
  if (line.startsWith("/")) {
    try { const d = await api({ action: "cmd", text: line }); tw((d.texts || []).join("\n\n") || "Готово"); } catch (e) { tw("✖ " + e.message, "e"); }
    return;
  }
  if (cmdl === "help" || cmdl === "помощь") return tw(TERM_HELP, "m");
  if (cmdl === "clear") { $("#termOut").innerHTML = ""; return; }
  if (cmdl === "ls") {
    const d = await term("ls", { path: arg }); if (!d) return;
    const base = arg.replace(/^\/+|\/+$/g, "");
    if (base) twLink("📁 ..", "ls " + base.split("/").slice(0, -1).join("/"));
    d.files.forEach(f => { const full = (base ? base + "/" : "") + f.name;
      f.type === "dir" ? twLink("📁 " + f.name + "/", "ls " + full) : twLink("📄 " + f.name + "  " + kb(f.size), "edit " + full); });
    return;
  }
  if (cmdl === "find") {
    if (!arg) return tw("Напишите, что искать: find promo", "e");
    const d = await term("find", { q: arg }); if (!d) return;
    if (!d.paths.length) return tw("Ничего не нашлось", "m");
    d.paths.forEach(p => twLink("📄 " + p, "edit " + p));
    return;
  }
  if (cmdl === "cat") {
    const d = await term("cat", { path: arg }); if (!d) return;
    if (d.binary) return tw(`Это не текстовый файл (${kb(d.size)}). Скачать: ${d.url}`, "m");
    const lines = d.text.split("\n");
    tw(lines.slice(0, 400).join("\n") + (lines.length > 400 ? `\n… ещё ${lines.length - 400} строк — откройте edit ${arg}` : ""));
    return;
  }
  if (cmdl === "edit" || cmdl === "new") {
    if (!arg) return tw("Укажите файл: " + cmdl + " index.html", "e");
    let d = { text: "", sha: null };
    if (cmdl === "edit") { d = await term("cat", { path: arg }); if (!d) return; if (d.binary) return tw("Это не текстовый файл — редактировать здесь нельзя.", "e"); }
    if (d.note) tw(d.note, "m");
    return openEditor(arg.replace(/^\/+/, ""), d.text, d.sha);
  }
  if (cmdl === "rm") {
    if (!arg) return tw("Укажите файл: rm старый.txt", "e");
    return confirmBox("Удалить файл?", `«${esc(arg)}» будет удалён. Вернуть можно через history → restore.`, "🗑 Удалить", "red",
      () => confirmBox("Точно удалить?", "Второе подтверждение.", "🗑 Да, удалить", "red", async () => { closeSheet(); const d = await term("rm", { path: arg }); if (d) tw(d.msg, "c"); }));
  }
  if (cmdl === "history") {
    const d = await term("history", { path: arg }); if (!d) return;
    d.commits.forEach(x => { const el = tw(`${x.sha.slice(0, 7)}  ${dt(x.date)}  ${x.msg}`); if (arg) { el.className = "l"; el.onclick = () => { $("#termIn").value = `restore ${arg} ${x.sha}`; $("#termIn").focus(); }; } });
    if (arg) tw("Нажмите на строку — подставится команда возврата к этой версии.", "m");
    else tw("history <файл> — изменения конкретного файла и откат.", "m");
    return;
  }
  if (cmdl === "restore") {
    const [path, ref] = rest;
    if (!path || !ref) return tw("Нужно: restore <файл> <версия> (версию возьмите из history <файл>)", "e");
    return confirmBox("Вернуть старую версию?", `«${esc(path)}» станет таким, каким был в версии ${esc(ref.slice(0, 7))}. Текущая версия останется в истории.`, "↩️ Вернуть", "",
      async () => { closeSheet(); const d = await term("restore", { path, ref }); if (d) tw(d.msg, "c"); });
  }
  if (cmdl === "runs") {
    const d = await term("runs"); if (!d) return;
    d.runs.forEach(r => tw(`${r.status !== "completed" ? "⏳" : r.result === "success" ? "✅" : "❌"} ${dt(r.date)}  ${r.name}: ${r.title}`, r.result === "failure" ? "e" : ""));
    return;
  }
  if (cmdl === "sync") { const d = await term("sync"); if (d) tw(d.msg, "c"); return; }
  tw(`Не знаю команду «${c}». help — список команд. Команды бота пишутся со слэшем: /статус`, "e");
}
function openEditor(path, text, sha){
  sheet(`<div class="top"><h3 style="font-family:ui-monospace,monospace;font-size:14px;word-break:break-all">✏️ ${esc(path)}</h3><button class="x" id="edX">×</button></div>
    <div style="display:flex;gap:6px;margin-bottom:8px"><input id="edQ" placeholder="Найти в файле" style="flex:1"><button class="btn ghost sm" id="edFind">Найти ↓</button></div>
    <textarea class="editor" id="edT" spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off"></textarea>
    <div class="muted" id="edInfo" style="font-size:12px;margin:6px 2px"></div>
    <div class="btns full" id="edBtns"><button class="btn" id="edSave">💾 Сохранить</button><button class="btn ghost" id="edCancel">Закрыть</button></div>
    <div class="card" id="edAsk" hidden><p id="edAskT" style="margin:0 0 10px"></p><div class="btns full"><button class="btn" id="edYes"></button><button class="btn ghost" id="edNo">Вернуться к правке</button></div></div>`);
  editorOpen = true;
  const t = $("#edT"); t.value = text;
  const info = () => { const n = t.value.split("\n").length; $("#edInfo").textContent = `${n} строк · ${kb(new Blob([t.value]).size)}${t.value !== text ? " · есть несохранённые изменения" : ""}`; };
  info(); t.oninput = info;
  const ask = (msg, yes, cls, fn) => { $("#edAskT").innerHTML = msg; $("#edYes").textContent = yes; $("#edYes").className = "btn " + cls;
    $("#edBtns").hidden = true; $("#edAsk").hidden = false; $("#edYes").onclick = fn; $("#edAsk").scrollIntoView({ block: "end" }); };
  $("#edNo").onclick = () => { $("#edAsk").hidden = true; $("#edYes").hidden = false; $("#edBtns").hidden = false; };
  const done = () => { editorOpen = false; closeSheet(); };
  const quit = () => t.value === text ? done() : ask("Закрыть без сохранения? Изменения пропадут.", "Закрыть", "red", done);
  $("#edX").onclick = quit; $("#edCancel").onclick = quit;
  $("#edFind").onclick = () => { const q = $("#edQ").value; if (!q) return;
    let i = t.value.toLowerCase().indexOf(q.toLowerCase(), t.selectionEnd); if (i < 0) i = t.value.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return toast("Не найдено");
    t.focus(); t.setSelectionRange(i, i + q.length);
    t.scrollTop = Math.max(0, (t.value.slice(0, i).split("\n").length - 5) * 18); };
  $("#edSave").onclick = () => {
    if (t.value === text && sha) return toast("Изменений нет");
    const a = text.split("\n"), b = t.value.split("\n"); let ch = 0;
    for (let k = 0; k < Math.max(a.length, b.length); k++) if (a[k] !== b[k]) ch++;
    const val = t.value;
    ask(`Сохранить <b>${esc(path)}</b>? Изменено строк ≈ ${ch}. Изменение попадёт на сайт через 1–3 минуты. Откатить можно: history ${esc(path)}.`, "💾 Да, сохранить", "",
      async () => {
        $("#edYes").disabled = true;
        try {
          const d = await api({ action: "term", op: "write", path, text: val, sha });
          done(); tw(d.data.msg, "c");
        } catch (e) { $("#edAskT").innerHTML = "❌ " + esc(e.message); $("#edYes").hidden = true; }
        finally { $("#edYes").disabled = false; }
      });
  };
}
$("#termGo").onclick = () => { const v = $("#termIn").value; $("#termIn").value = ""; termRun(v); };
$("#termIn").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); $("#termGo").onclick(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); if (termPos < termHist.length - 1) $("#termIn").value = termHist[++termPos]; }
  else if (e.key === "ArrowDown") { e.preventDefault(); termPos = Math.max(-1, termPos - 1); $("#termIn").value = termPos < 0 ? "" : termHist[termPos]; }
});
$$("#termChips [data-t]").forEach(b => b.onclick = () => { const v = b.dataset.t; if (v.endsWith(" ")) { $("#termIn").value = v; $("#termIn").focus(); } else termRun(v); });

function renderLinks(){
  const q = $("#lq").value.trim().toLowerCase();
  const list = [...D.links].reverse().filter(l => !q || [l.title, l.note, l.site, l.url, (l.tags || []).join(" ")].join(" ").toLowerCase().includes(q)).slice(0, 80);
  $("#links").innerHTML = list.map(l => `<a class="row" href="${esc(l.url)}" target="_blank" rel="noopener" style="text-decoration:none">
      <div class="t"><b>${esc(l.title || l.note || l.url)}</b><small>№${l.id} · ${esc(l.site || "")} ${(l.tags || []).map(t => "#" + esc(t)).join(" ")}${l.status === "done" ? " · ✅ напечатано" : ""}</small></div></a>`).join("")
    || `<div class="empty">${D.links.length ? "Ничего не нашлось" : "Ссылок пока нет"}</div>`;
}

$("#metrBtn").onclick = () => goTab("metr");

/* ---------- Метрика ---------- */
let mP = "7", mTempAll = false; const MC = {};
const iso = d => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
const ruD = s => s.split("-").reverse().join(".");
function periodRange(p){
  const t = new Date(), d = n => { const x = new Date(t); x.setDate(x.getDate() - n); return x; };
  if (p === "today") return [iso(t), iso(t)];
  if (p === "yesterday") return [iso(d(1)), iso(d(1))];
  if (p === "month") return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)];
  if (p === "prevmonth") return [iso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), iso(new Date(t.getFullYear(), t.getMonth(), 0))];
  if (p === "custom") return [$("#mFrom").value, $("#mTo").value];
  return [iso(d(+p - 1)), iso(t)];
}
const UTM = { flyer: "Листовка (QR-код)", max_channel: "Посты в канале MAX", bot: "Бот", promo: "Пост с промокодом", channel_post: "Пост про сайт", qr: "QR-код" };
const SRC = { "Direct traffic": "Прямые заходы", "Link traffic": "Переходы по ссылкам", "Search engine traffic": "Из поиска", "Social network traffic": "Из соцсетей",
  "Internal traffic": "Внутри сайта", "Messenger traffic": "Из мессенджеров", "Cached page traffic": "Из сохранённых копий", "Recommendation system traffic": "Из рекомендаций",
  "Переходы из мессенджеров": "Из мессенджеров (MAX, Telegram)", "Прямые заходы": "Прямые заходы (адрес, QR-код, закладки)", "Внутренние переходы": "Переходы внутри сайта",
  "Переходы из поисковых систем": "Из поиска (Яндекс, Google)", "Переходы по ссылкам на сайтах": "По ссылкам с других сайтов", "Переходы из социальных сетей": "Из соцсетей" };
$$("#mSeg button").forEach(b => b.onclick = () => {
  $$("#mSeg button").forEach(x => x.classList.toggle("on", x === b));
  mP = b.dataset.p;
  $("#mCustom").hidden = mP !== "custom";
  if (mP === "custom") {
    if (!$("#mFrom").value) { const [a, z] = periodRange("30"); $("#mFrom").value = a; $("#mTo").value = z; }
    return;
  }
  loadMetrika();
});
$("#mApply").onclick = () => {
  if (!$("#mFrom").value || !$("#mTo").value) return toast("Выберите обе даты");
  loadMetrika();
};
$$('nav button[data-t="metr"]').forEach(b => b.addEventListener("click", () => { if (!$("#mBody .card, #mBody .kpi3")) loadMetrika(); }));
async function loadMetrika(){
  const [d1, d2] = periodRange(mP);
  const lo = d1 < d2 ? d1 : d2, hi = d1 < d2 ? d2 : d1;
  const key = lo + "_" + hi + (mTempAll ? "_all" : "");
  $("#mRange").textContent = lo === hi ? "За " + ruD(lo) : "С " + ruD(lo) + " по " + ruD(hi);
  if (MC[key]) return renderMetrika(MC[key]);
  $("#mBody").innerHTML = `<div class="empty">Загружаю статистику…</div>`;
  try {
    const days = Math.round((new Date(hi) - new Date(lo)) / 864e5) + 1;
    const d = await api({ action: "metrika_data", days, date1: lo, date2: hi, all: mTempAll });
    if (d.data.error) { $("#mBody").innerHTML = `<div class="card">${esc(d.data.error)}</div>`; return; }
    MC[key] = d.data; renderMetrika(d.data);
  } catch (e) { $("#mBody").innerHTML = `<div class="card">Не удалось загрузить: ${esc(e.message)}</div>`; }
}
function hbars(rows, map){
  if (!rows || !rows.length) return `<div class="muted">Пока нет данных</div>`;
  const mx = Math.max(...rows.map(r => r[1]), 1);
  return `<div class="hbars">${rows.map(([n, v]) => `<div class="hb"><span title="${esc(n)}">${esc(map ? (map[n] || n) : n)}</span><b>${v}</b><i><u style="width:${Math.max(2, Math.round(v / mx * 100))}%"></u></i></div>`).join("")}</div>`;
}
function cols(id, rows, fmt){
  const mx = Math.max(...rows.map(r => r.v), 1);
  return `<div class="readout" id="${id}R">Нажмите на столбик, чтобы увидеть значение</div>
    <div class="cols" id="${id}">${rows.map((r, k) => `<div data-k="${k}"><i style="height:${Math.max(1.5, r.v / mx * 100)}%"></i></div>`).join("")}</div>
    <div class="axis"><span>${esc(rows.length ? rows[0].label : "")}</span><span>${esc(rows.length ? rows[rows.length - 1].label : "")}</span></div>`;
}
function bindCols(id, rows, fmt){
  const box = $("#" + id); if (!box) return;
  const show = el => { if (!el || !el.dataset.k) return; $$("#" + id + " div").forEach(x => x.classList.toggle("on", x === el)); $("#" + id + "R").innerHTML = fmt(rows[+el.dataset.k]); };
  box.addEventListener("pointerover", e => show(e.target.closest("[data-k]")));
  box.addEventListener("click", e => show(e.target.closest("[data-k]")));
}
const mmss = s => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
function renderMetrika(m){
  const t = m.totals;
  const daily = m.daily.map(([d, u, v]) => ({ v: u, visits: v, label: d.slice(8, 10) + "." + d.slice(5, 7) }));
  const hours = Array.from({ length: 24 }, (_, h) => { const r = m.hours.find(x => x[0] === h); return { v: r ? r[1] : 0, label: h + ":00" }; });
  const best = [...hours].sort((a, b) => b.v - a.v)[0];
  $("#mBody").innerHTML = `
    <div class="kpi3">
      <div class="card kpi"><b>${t.users}</b><span>человек зашло</span></div>
      <div class="card kpi"><b>${t.visits}</b><span>заходов всего</span></div>
      <div class="card kpi"><b>${t.pageviews}</b><span>открыто страниц</span></div>
      <div class="card kpi"><b>${t.new}%</b><span>пришли впервые</span></div>
      <div class="card kpi"><b>${t.bounce}%</b><span>сразу ушли</span></div>
      <div class="card kpi"><b>${mmss(t.duration)}</b><span>мин:сек на сайте</span></div>
    </div>
    <p class="hint" style="margin-top:8px">«Человек» — разные посетители, «заходов» — сколько раз заходили (один человек может зайти несколько раз). «Сразу ушли» — закрыли сайт меньше чем за 15 секунд.</p>
    ${daily.length > 1 ? `<h2>Посетители по дням</h2><div class="card">${cols("cDay", daily)}</div>` : ""}
    <h2>Что делали на сайте</h2>
    <p class="hint">Сколько раз нажимали кнопки: открыть модель, заказать, позвонить, поделиться…</p>
    <div class="card">${hbars(m.goals)}</div>
    <h2>Какие модели открывали</h2>
    <p class="hint">Самые интересные покупателям модели — сколько раз открывали карточку.</p>
    <div class="card">${hbars(m.models)}</div>
    <h2>Откуда пришли</h2>
    <p class="hint">«Из мессенджеров» — по ссылке из MAX или Telegram, «прямые» — набрали адрес или с QR-кода, «из поиска» — через Яндекс/Google.</p>
    <div class="card">${hbars(m.sources, SRC)}</div>
    <h2>С каких сайтов и приложений</h2>
    <div class="card">${hbars(m.engines)}</div>
    <h2>Наши ссылки</h2>
    <p class="hint">Сколько заходов принесли листовка, посты в канале и бот.</p>
    <div class="card">${hbars(m.utm, UTM)}</div>
    <h2>Когда заходят (по часам)</h2>
    <div class="card">${cols("cHour", hours)}<div class="muted" style="margin-top:6px">${best && best.v ? "Больше всего заходов около " + best.label + " — хорошее время для постов." : ""}</div></div>
    <h2>Устройства</h2>
    <div class="card">${hbars(m.devices)}</div>
    <h2>Города</h2>
    <div class="card">${hbars(m.cities)}</div>
    <h2>Что искали в поисковиках</h2>
    <div class="card">${hbars(m.phrases)}</div>
    <h2>Начать отсчёт заново</h2>
    <div class="card form">
      ${m.tempAll ? `<div class="pill o" style="justify-self:start">👁 Сейчас временно показано всё — отсчёт с ${esc(m.from)} не применён</div>` : ""}
      <div class="muted">${m.from ? "Статистика считается с <b>" + esc(m.from) + "</b> (по Уфе)." + (m.approx ? " Метрика не дала отсечь по минутам — показано с начала этого дня." : "") : "Статистика считается за весь выбранный период."}
        Сами данные в Метрике не удаляются — здесь просто не показывается то, что было раньше (например, ваши тестовые заходы).</div>
      <div class="btns full"><button class="btn red" id="mReset">🧹 Начать отсчёт с этого момента</button>${m.from ? `<button class="btn ghost" id="mTemp">${mTempAll ? "↩️ Вернуть отсчёт" : "👁 Показать всё временно"}</button>` : ""}${m.from ? `<button class="btn ghost" id="mAll">Показывать всё</button>` : ""}</div>
    </div>
    <p class="muted" style="font-size:13px">Ваши заходы и заходы админов с телефона, где открыто это приложение, больше не считаются. Данные Яндекс Метрики обновляются с задержкой до нескольких минут.</p>`;
  const confirmSheet = (title, text, yes, cls, then) => {
    sheet(`<div class="top"><h3>${title}</h3><button class="x" onclick="__appCloseSheet()">×</button></div><p>${text}</p>
      <div class="btns full"><button class="btn ${cls}" id="cfYes">${yes}</button><button class="btn ghost" onclick="__appCloseSheet()">Отмена</button></div>`);
    $("#cfYes").onclick = then;
  };
  const resetNow = () => { closeSheet(); mTempAll = false; for (const k in MC) delete MC[k]; cmd("/метрика сброс", false).then(loadMetrika); };
  const showAll = () => { closeSheet(); mTempAll = false; for (const k in MC) delete MC[k]; cmd("/метрика всё", false).then(loadMetrika); };
  $("#mReset").onclick = () => confirmSheet("Начать отсчёт с этого момента?",
    "Во вкладке «Метрика» и в статистике бота будут только заходы начиная с этой минуты — все цифры станут нулями.",
    "Да, продолжить", "red",
    () => confirmSheet("Точно начать заново?",
      "Это второе подтверждение. Прошлые данные не удалятся — их можно будет снова показать кнопкой «Показывать всё».",
      "🧹 Да, начать с этого момента", "red", resetNow));
  if ($("#mTemp")) $("#mTemp").onclick = () => { mTempAll = !mTempAll; loadMetrika(); };   // без подтверждения: настройка не меняется
  if ($("#mAll")) $("#mAll").onclick = () => confirmSheet("Показывать всю статистику?",
    "Снова будут учитываться все заходы за выбранный период, включая те, что были до отсчёта (например, ваши тестовые).",
    "Да, продолжить", "",
    () => confirmSheet("Точно показать всё?", "Это второе подтверждение. Отсчёт «с этого момента» будет отменён.",
      "📈 Да, показывать всё", "", showAll));
  bindCols("cDay", daily, r => `<b>${esc(r.label)}</b>: ${r.v} посетителей, ${r.visits} визитов`);
  bindCols("cHour", hours, r => `<b>${esc(r.label)}</b>: ${r.v} визитов`);
}

/* ---------- старт ---------- */
try { localStorage.setItem("nometrika", "1"); document.cookie = "nometrika=1; max-age=315360000; path=/"; } catch(e){}   // заходы админов не считаем
try { WA && WA.ready && WA.ready(); } catch(e){}
(async () => {
  for (let k = 0; !INIT && k < 20; k++) {             // мост MAX иногда отдаёт данные не сразу
    await new Promise(r => setTimeout(r, 150));
    INIT = (window.WebApp && window.WebApp.initData) || "";
  }
  const u = window.WebApp && window.WebApp.initDataUnsafe && window.WebApp.initDataUnsafe.user;
  if (u) $("#who").textContent = "Здравствуйте, " + (u.first_name || "") + "!";
  if (!INIT) gate("Откройте из бота", "Это приложение управления работает внутри MAX — откройте его кнопкой в чате с ботом.", true);
  else load(true);
})();

window.__appCloseSheet = closeSheet; window.__appLoad = load;   // для кнопок в разметке
})();
