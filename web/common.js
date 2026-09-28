"use strict";

// Everything the three pages share: language, fetching, formatting, header.

const STRINGS = {
	es: {
		tagline: "estadísticas anónimas",
		projects: "Proyectos",
		projects_lead: "Uso anónimo de mis proyectos de código abierto. Solo cifras agregadas: ninguna instalación se puede identificar.",
		active_30: "activas · 30 días",
		active_7: "activas · 7 días",
		no_projects: "Aún no hay proyectos.",
		loading: "Cargando…",
		error: "No se han podido cargar los datos.",
		active: "Instalaciones activas",
		new: "Nuevas en el periodo",
		latest: "En la última versión",
		vs_previous: "vs periodo anterior",
		per_install: "media {n} por instalación",
		daily_title: "Instalaciones activas por día",
		daily_sub: "Instalaciones distintas que enviaron datos ese día · discontinua: activas en los 7 días anteriores",
		daily: "Ese día",
		monthly_title: "Instalaciones activas por mes",
		monthly_sub: "Desde el primer mes con datos · solo meses completos",
		monthly_new: "Nuevas",
		monthly_retained: "Repiten del mes anterior",
		rolling: "Últimos 7 días",
		versions: "Versiones",
		versions_sub: "última versión enviada",
		arch: "Arquitectura",
		arch_sub: "del equipo donde corre",
		adoption: "Ajustes y funciones",
		adoption_sub: "% de las instalaciones que lo envían",
		usage_sub: "% de instalaciones activas que lo usaron al menos una vez en el periodo",
		times: "{n} veces",
		show_all: "Ver los {n}",
		show_less: "Ver menos",
		reported_by: "enviado por {n}",
		updated: "Actualizado {t}",
		privacy: "Qué se recoge",
		source: "Código",
		stats: "Estadísticas",
		back: "Todos los proyectos",
		no_data: "Sin datos en este periodo.",
		privacy_title: "Qué se recoge",
		privacy_lead: "Todo lo que {name} puede enviar. El servidor descarta cualquier dato que no aparezca en esta lista.",
		common_fields: "En todos los envíos",
		install_id_desc: "Un identificador aleatorio que se crea en el primer envío. No se deriva de nada tuyo ni de tu equipo: solo sirve para no contar dos veces la misma instalación.",
		version_desc: "La versión del proyecto.",
		arch_desc: "La arquitectura del procesador (amd64, arm64…).",
		metrics_title: "Estado de la instalación",
		usage_title: "Uso",
		never_title: "Nunca se guarda",
		never_items: ["Tu dirección IP: solo se usa en memoria para limitar abusos, y se olvida en una hora.", "Nombres, IDs de Telegram, rutas, direcciones de hosts, nombres de contenedores o imágenes.", "Nada de lo que escribes."],
		how_often: "Se envía como mucho una vez cada {h} horas. Cada envío se borra a los {d} días; lo que queda después son solo totales por mes, sin ningún identificador.",
		opt_out: "Cómo desactivarlo",
		type_text: "texto",
		type_int: "número",
		type_number: "número",
		type_bool: "sí / no",
		type_enum: "uno de: {v}",
	},
	en: {
		tagline: "anonymous statistics",
		projects: "Projects",
		projects_lead: "Anonymous usage of my open source projects. Aggregate figures only: no installation can be identified.",
		active_30: "active · 30 days",
		active_7: "active · 7 days",
		no_projects: "No projects yet.",
		loading: "Loading…",
		error: "The data could not be loaded.",
		active: "Active installations",
		new: "New this period",
		latest: "On the latest version",
		vs_previous: "vs previous period",
		per_install: "{n} per installation on average",
		daily_title: "Active installations per day",
		daily_sub: "Distinct installations that reported that day · dashed: active in the previous 7 days",
		daily: "That day",
		monthly_title: "Active installations per month",
		monthly_sub: "Since the first month with data · complete months only",
		monthly_new: "New",
		monthly_retained: "Back from the month before",
		rolling: "Last 7 days",
		versions: "Versions",
		versions_sub: "latest version reported",
		arch: "Architecture",
		arch_sub: "of the machine it runs on",
		adoption: "Settings and features",
		adoption_sub: "% of the installations that report it",
		usage_sub: "% of active installations that used it at least once in the period",
		times: "{n} times",
		show_all: "Show all {n}",
		show_less: "Show less",
		reported_by: "reported by {n}",
		updated: "Updated {t}",
		privacy: "What is collected",
		source: "Source",
		stats: "Statistics",
		back: "All projects",
		no_data: "No data in this period.",
		privacy_title: "What is collected",
		privacy_lead: "Everything {name} may send. The server discards anything not on this list.",
		common_fields: "In every report",
		install_id_desc: "A random identifier created on the first report. It is not derived from you or your machine: it only keeps one installation from being counted twice.",
		version_desc: "The project's version.",
		arch_desc: "The processor architecture (amd64, arm64…).",
		metrics_title: "Installation state",
		usage_title: "Usage",
		never_title: "Never stored",
		never_items: ["Your IP address: it is only held in memory to limit abuse, and forgotten within an hour.", "Names, Telegram IDs, paths, host addresses, container or image names.", "Anything you type."],
		how_often: "Sent at most once every {h} hours. Each report is deleted after {d} days; what is left after that is only monthly totals, with no identifier at all.",
		opt_out: "How to turn it off",
		type_text: "text",
		type_int: "number",
		type_number: "number",
		type_bool: "yes / no",
		type_enum: "one of: {v}",
	},
};

const RETENTION_DAYS = 90;

function storedLanguage() {
	try { return localStorage.getItem("lang"); } catch (e) { return null; }
}

function currentLanguage() {
	const stored = storedLanguage();
	if (stored === "es" || stored === "en") return stored;
	const browser = (navigator.language || "en").toLowerCase();
	return /^(es|ca|gl)/.test(browser) ? "es" : "en";
}

const LANG = currentLanguage();
document.documentElement.lang = LANG;

function t(key, values) {
	let value = (STRINGS[LANG] && STRINGS[LANG][key]) || STRINGS.en[key] || key;
	if (values && typeof value === "string") {
		for (const [name, replacement] of Object.entries(values)) value = value.split(`{${name}}`).join(replacement);
	}
	return value;
}

function escapeHtml(value) {
	return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => (
		{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const numberFormat = new Intl.NumberFormat(LANG === "es" ? "es-ES" : "en-GB");
const decimalFormat = new Intl.NumberFormat(LANG === "es" ? "es-ES" : "en-GB", { maximumFractionDigits: 1 });

function fmt(n) { return numberFormat.format(Math.round(n)); }
function fmt1(n) { return decimalFormat.format(n); }
function pct(share) { return `${decimalFormat.format(share * 100)} %`; }

async function api(path) {
	const separator = path.includes("?") ? "&" : "?";
	const response = await fetch(`${path}${separator}lang=${LANG}`);
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	return response.json();
}

function css(name) {
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function palette() {
	return [1, 2, 3, 4, 5, 6, 7].map((n) => css(`--series-${n}`));
}

function projectFromPath() {
	return decodeURIComponent(location.pathname.split("/").filter(Boolean)[0] || "");
}

function renderHeader() {
	const header = document.getElementById("top");
	header.innerHTML = `
		<a class="brand" href="/"><span class="dot"></span>dgongut <small>· ${escapeHtml(t("tagline"))}</small></a>
		<div class="segmented" role="group" aria-label="Language">
			<button type="button" data-lang="es" aria-pressed="${LANG === "es"}">ES</button>
			<button type="button" data-lang="en" aria-pressed="${LANG === "en"}">EN</button>
		</div>`;
	header.querySelectorAll("[data-lang]").forEach((button) => {
		button.addEventListener("click", () => {
			try { localStorage.setItem("lang", button.dataset.lang); } catch (e) { /* private mode */ }
			location.reload();
		});
	});
}

function applyChartDefaults() {
	if (!window.Chart) return;
	Chart.defaults.color = css("--text-secondary");
	Chart.defaults.borderColor = css("--border");
	Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
	Chart.defaults.font.size = 12;
	Chart.defaults.plugins.legend.display = false;
	Chart.defaults.maintainAspectRatio = false;
}

function showError(target) {
	document.getElementById(target).innerHTML = `<p class="empty">${escapeHtml(t("error"))}</p>`;
}

renderHeader();
applyChartDefaults();
