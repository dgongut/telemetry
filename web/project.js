"use strict";

// One project's dashboard, drawn entirely from its summary: the page knows no
// project and no metric by name, so a new manifest needs no change here.

const PROJECT = projectFromPath();
const USAGE_PREVIEW = 12;
let charts = [];
let days = 30;
try { days = Number(sessionStorage.getItem(`days:${PROJECT}`)) || 30; } catch (e) { /* private mode */ }
if (![7, 30, 90].includes(days)) days = 30;

function delta(current, previous) {
	if (!previous) return `<span class="delta">&nbsp;</span>`;
	const change = (current - previous) / previous;
	const direction = change > 0 ? "up" : change < 0 ? "down" : "";
	const sign = change > 0 ? "+" : "";
	return `<span class="delta ${direction}">${sign}${escapeHtml(pct(change))} ${escapeHtml(t("vs_previous"))}</span>`;
}

function kpi(label, value, extra) {
	return `<div class="kpi"><div class="label">${escapeHtml(label)}</div><div class="value">${value}</div>${extra || ""}</div>`;
}

function barRows(items, colorIndex) {
	const color = palette()[colorIndex || 0];
	return items.map((item) => `
		<div class="bar-row" title="${escapeHtml(item.title || "")}">
			<span class="name">${item.html}</span>
			<span class="track"><span class="fill" style="width:${Math.max(0, Math.min(100, item.share * 100))}%;background:${color}"></span></span>
			<span class="pct">${escapeHtml(pct(item.share))}</span>
		</div>`).join("");
}

function card(title, sub, body, extraClass) {
	return `<section class="card ${extraClass || ""}"><h2>${escapeHtml(title)}</h2>${sub ? `<p class="sub">${escapeHtml(sub)}</p>` : ""}${body}</section>`;
}

function chartBox(id, small) {
	return `<div class="chart ${small ? "small" : ""}"><canvas id="${id}"></canvas></div>`;
}

function drawDonut(id, entries) {
	const colors = palette();
	charts.push(new Chart(document.getElementById(id), {
		type: "doughnut",
		data: { labels: entries.map((e) => e.label), datasets: [{
			data: entries.map((e) => e.count), backgroundColor: entries.map((_, i) => colors[i % colors.length]), borderWidth: 0 }] },
		options: { cutout: "62%", plugins: { legend: { display: true, position: "right",
			labels: { boxWidth: 10, boxHeight: 10, padding: 8 } } } },
	}));
}

function drawColumns(id, entries, colorIndex) {
	const total = entries.reduce((sum, e) => sum + e.count, 0) || 1;
	charts.push(new Chart(document.getElementById(id), {
		type: "bar",
		data: { labels: entries.map((e) => e.label), datasets: [{
			data: entries.map((e) => e.count / total * 100), backgroundColor: palette()[colorIndex || 0], borderRadius: 4 }] },
		options: {
			scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { callback: (v) => `${v} %` } } },
			plugins: { tooltip: { callbacks: { label: (c) => `${pct(c.raw / 100)} · ${fmt(entries[c.dataIndex].count)}` } } },
		},
	}));
}

function drawSeries(series) {
	const colors = palette();
	const labels = series.days.map((day) => {
		const [, month, date] = day.split("-");
		return `${Number(date)}/${Number(month)}`;
	});
	charts.push(new Chart(document.getElementById("series"), {
		type: "line",
		data: { labels, datasets: [
			{ label: t("daily"), data: series.daily, borderColor: colors[0], backgroundColor: colors[0] + "22",
				fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
			{ label: t("rolling"), data: series.rolling7, borderColor: colors[1], borderDash: [5, 4],
				tension: 0.3, pointRadius: 0, borderWidth: 1.5 },
		] },
		options: {
			interaction: { mode: "index", intersect: false },
			scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, autoSkip: true } }, y: { beginAtZero: true } },
		},
	}));
}

function drawMonthly(months) {
	const colors = palette();
	const locale = LANG === "es" ? "es-ES" : "en-GB";
	const labels = months.map((m) => {
		const [year, month] = m.month.split("-").map(Number);
		return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString(locale, { month: "short", year: "2-digit", timeZone: "UTC" });
	});
	charts.push(new Chart(document.getElementById("monthly"), {
		type: "bar",
		data: { labels, datasets: [
			{ label: t("active"), data: months.map((m) => m.active), backgroundColor: colors[0], borderRadius: 4 },
			{ label: t("monthly_new"), data: months.map((m) => m.new), backgroundColor: colors[1], borderRadius: 4 },
			{ label: t("monthly_retained"), data: months.map((m) => m.retained), backgroundColor: colors[2], borderRadius: 4 },
		] },
		options: {
			interaction: { mode: "index", intersect: false },
			scales: { x: { grid: { display: false } }, y: { beginAtZero: true } },
			plugins: { legend: { display: true, position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } } },
		},
	}));
}

function usageCard(group, index) {
	const items = group.items.map((item) => ({
		html: `${escapeHtml(item.label)}`,
		title: `${item.key} · ${t("times", { n: fmt(item.total) })}`,
		share: item.share,
	}));
	const hidden = items.length > USAGE_PREVIEW;
	const body = `<div class="bars" data-usage="${index}">${barRows(hidden ? items.slice(0, USAGE_PREVIEW) : items, index % 3)}</div>` +
		(hidden ? `<button type="button" class="more" data-more="${index}">${escapeHtml(t("show_all", { n: items.length }))}</button>` : "");
	return { html: card(group.label, group.description ? `${group.description} ${t("usage_sub")}` : t("usage_sub"), body), items };
}

function render(data) {
	charts.forEach((chart) => chart.destroy());
	charts = [];
	const target = document.getElementById("dashboard");
	const k = data.kpis;
	const metrics = data.metrics;

	const kpis = [
		kpi(t("active"), fmt(k.active), delta(k.active, k.active_previous)),
		kpi(t("new"), fmt(k.new), delta(k.new, k.new_previous)),
	];
	for (const metric of metrics) {
		if (!metric.kpi) continue;
		const extra = metric.kpi.kind === "sum"
			? `<span class="delta">${escapeHtml(t("per_install", { n: fmt1(metric.kpi.mean) }))}</span>` : "";
		kpis.push(kpi(metric.kpi_label, metric.kpi.kind === "sum" ? fmt(metric.kpi.value) : fmt1(metric.kpi.value), extra));
	}
	if (k.latest_version) {
		kpis.push(kpi(t("latest"), escapeHtml(pct(k.latest_version_share)),
			`<span class="delta">${escapeHtml(k.latest_version)}</span>`));
	}

	if (!k.active) {
		target.innerHTML = `<div class="grid kpis">${kpis.join("")}</div><p class="empty">${escapeHtml(t("no_data"))}</p>`;
		return;
	}

	const usage = data.usage.map(usageCard);
	const charted = metrics.filter((m) => m.reported && ["histogram", "bar", "donut"].includes(m.chart));
	const adoption = metrics.filter((m) => m.reported && (m.chart === "adoption" || m.chart === "nonzero"))
		.sort((a, b) => b.share - a.share);

	const small = [
		card(t("versions"), t("versions_sub"), chartBox("versions", true)),
		card(t("arch"), t("arch_sub"), chartBox("arch", true)),
		...charted.map((m, i) => card(m.label, t("reported_by", { n: fmt(m.reported) }), chartBox(`metric-${i}`, true))),
	];

	target.innerHTML = `
		<div class="grid kpis">${kpis.join("")}</div>
		${card(t("daily_title"), t("daily_sub"), chartBox("series"))}
		${data.monthly.length >= 2 ? card(t("monthly_title"), t("monthly_sub"), chartBox("monthly")) : ""}
		${usage.map((u) => u.html).join("")}
		<div class="grid two">${small.join("")}</div>
		${adoption.length ? card(t("adoption"), t("adoption_sub"), `<div class="bars">${barRows(adoption.map((m) => ({
			html: escapeHtml(m.label), title: `${m.description} · ${t("reported_by", { n: fmt(m.reported) })}`, share: m.share,
		})), 1)}</div>`) : ""}`;

	drawSeries(data.series);
	if (data.monthly.length >= 2) drawMonthly(data.monthly);
	drawDonut("versions", data.versions);
	drawDonut("arch", data.arch);
	charted.forEach((metric, i) => {
		const id = `metric-${i}`;
		if (metric.chart === "donut") drawDonut(id, metric.data.filter((e) => e.count));
		else drawColumns(id, metric.data, i % 2 ? 2 : 1);
	});

	target.querySelectorAll("[data-more]").forEach((button) => {
		button.addEventListener("click", () => {
			const index = Number(button.dataset.more);
			const list = target.querySelector(`[data-usage="${index}"]`);
			const expanded = button.dataset.expanded === "1";
			const items = usage[index].items;
			list.innerHTML = barRows(expanded ? items.slice(0, USAGE_PREVIEW) : items, index % 3);
			button.dataset.expanded = expanded ? "0" : "1";
			button.textContent = expanded ? t("show_all", { n: items.length }) : t("show_less");
		});
	});
}

function header(project) {
	document.title = `${project.name} · stats`;
	document.getElementById("name").textContent = project.name;
	document.getElementById("description").textContent = project.description;
	const links = [`<a href="/">${escapeHtml(t("back"))}</a>`,
		`<a href="/${encodeURIComponent(project.id)}/privacy">${escapeHtml(t("privacy"))}</a>`];
	if (project.repo) links.push(`<a href="${escapeHtml(project.repo)}" rel="noopener">${escapeHtml(t("source"))}</a>`);
	document.getElementById("links").innerHTML = links.join(" · ");
}

function load() {
	document.querySelectorAll("#ranges [data-days]").forEach((button) => {
		button.setAttribute("aria-pressed", String(Number(button.dataset.days) === days));
	});
	api(`/v1/projects/${encodeURIComponent(PROJECT)}/summary?days=${days}`).then((data) => {
		header(data.project);
		render(data);
		const when = new Date(data.generated_at * 1000).toLocaleString(LANG === "es" ? "es-ES" : "en-GB",
			{ dateStyle: "medium", timeStyle: "short" });
		document.getElementById("footer").textContent = t("updated", { t: when });
	}).catch(() => showError("dashboard"));
}

document.querySelectorAll("#ranges [data-days]").forEach((button) => {
	button.addEventListener("click", () => {
		days = Number(button.dataset.days);
		try { sessionStorage.setItem(`days:${PROJECT}`, String(days)); } catch (e) { /* private mode */ }
		load();
	});
});

document.getElementById("dashboard").innerHTML = `<p class="empty">${escapeHtml(t("loading"))}</p>`;
load();
