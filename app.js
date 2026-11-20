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
  return `<div class="row ${i.hidden ? "dim" : ""}" data-no="${i.no}">
    <img src="${esc(thumb((i.photos || [])[0]) || "")}" alt="" loading="lazy">
    <div class="t"><b>${esc(i.title)}</b><small>№${i.no} · ${esc(i.category || "")}${i.hidden ? " · <span class='pill o'>скрыт</span>" : ""}</small></div>
    <div class="r">${right != null ? right : rub(priceOf(i))}</div></div>`;
}
function bindRows(sel){ $$(sel + " .row[data-no]").forEach(r => r.onclick = () => openItem(+r.dataset.no)); }
function renderItems(){
  const q = $("#q").value.trim().toLowerCase();
  let list = D.items.filter(i => !q || (i.title + " " + i.no).toLowerCase().includes(q));
  if (itemFilter === "hidden") list = list.filter(i => i.hidden);
  if (itemFilter === "views") list.sort((a, b) => (b.views || 0) - (a.views || 0));
  else if (itemFilter === "sold") list = list.filter(i => soldOf(i.no)).sort((a, b) => soldOf(b.no) - soldOf(a.no));
  else list.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  $("#items").innerHTML = list.map(i => rowItem(i, itemFilter === "views" ? `${i.views || 0} 👁` : itemFilter === "sold" ? `${soldOf(i.no)} шт.` : undefined)).join("")
    || `<div class="empty">Ничего не нашлось</div>`;
  bindRows("#items");
}
function sheet(html){ $("#sheet").innerHTML = html; $("#sheetBg").classList.add("on"); }
function closeSheet(){ $("#sheetBg").classList.remove("on"); }
$("#sheetBg").onclick = e => { if (e.target.id === "sheetBg") closeSheet(); };

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
    ${base.length > 1 ? `<h2>Главное фото</h2><div class="covers">${base.slice(0, 10).map((p, k) =>
      `<button class="${k + 1 === cover ? "on" : ""}" data-cmd="/обложка ${no} ${k + 1}"><img src="${esc(thumb(p))}" alt="Фото ${k + 1}"></button>`).join("")}</div>` : ""}
    <h2>Ещё</h2>
    <div class="btns full">
      ${i.hidden ? `<button class="btn" data-cmd="/показать ${no}">👁 Вернуть на сайт</button>` : `<button class="btn red" data-cmd="/скрыть ${no}">🙈 Скрыть с сайта</button>`}
      <a class="btn ghost" href="${esc(D.site + "#m" + no)}" target="_blank" rel="noopener">🌐 На сайте</a>
      ${i.url ? `<a class="btn ghost" href="${esc(i.url)}" target="_blank" rel="noopener">📣 Пост</a>` : ""}
    </div>`);
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
      : `<button class="btn ghost sm" data-revoke="${a.k}" data-name="${esc(a.name)}" data-full="${a.role === "full" ? 1 : ""}">Забрать</button>`;
    return `<div class="row" style="cursor:default"><div class="t"><b>${esc(a.name)}</b><small><span class="pill ${c}">${t}</span></small></div>${btn}</div>`; }).join("")
    || `<div class="empty">Список админов недоступен</div>`;
  renderLinks();
}
$("#admins").addEventListener("click", e => {      // забрать доступ — с подтверждением
  const b = e.target.closest("[data-revoke]"); if (!b) return;
  const k = b.dataset.revoke, name = b.dataset.name;
  sheet(`<div class="top"><h3>Забрать доступ?</h3><button class="x" onclick="__appCloseSheet()">×</button></div>
    <p>У «${esc(name)}» будет забран доступ${b.dataset.full ? " и полные права" : ""}. Он больше не сможет управлять ботом и открывать это приложение.</p>
    <div class="btns full"><button class="btn red" id="revYes">🚫 Да, забрать</button><button class="btn ghost" onclick="__appCloseSheet()">Отмена</button></div>`);
  $("#revYes").onclick = () => { closeSheet(); cmd(`/запретить ${k} да`); };
});
$("#lq").oninput = () => renderLinks();
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
