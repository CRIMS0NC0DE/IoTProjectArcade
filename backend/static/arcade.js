// Utilitários compartilhados pelas páginas do dashboard.

// Tags e nomes de jogos vêm de qualquer publisher do tópico: nunca entram crus no innerHTML.
function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const fmtScore = (n) => Number(n).toLocaleString("pt-BR");
const fmtDate = (epoch) =>
  new Date(epoch * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, "0")}min`;
  return m ? `${m}min` : `${seconds}s`;
}

const FIELD_HINTS = {
  username: "usuário: 3 a 20 letras minúsculas, números ou _",
  player_tag: "tag: 1 a 3 letras ou números",
  password: "senha: de 8 a 128 caracteres",
};

// fetch com JSON; lança Error com mensagem pronta para mostrar e o status HTTP.
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    let message = "Algo deu errado. Tente de novo.";
    if (typeof data?.detail === "string") message = data.detail;
    else if (res.status === 422 && Array.isArray(data?.detail)) {
      const fields = [...new Set(data.detail.map((e) => e.loc.at(-1)))];
      message = "Confira " + fields.map((f) => FIELD_HINTS[f] ?? f).join("; ");
    }
    throw Object.assign(new Error(message), { status: res.status });
  }
  return data;
}

// Canto do cabeçalho: jogador logado (link para o perfil) ou botão de entrar. Devolve o jogador, ou null.
async function renderNav(el) {
  const link = "text-yellow-400 hover:text-yellow-300";
  try {
    const me = await api("/api/me");
    el.innerHTML = `<a class="${link}" href="/perfil.html">▶ ${esc(me.player_tag)} · ${esc(me.username)}</a>`;
    return me;
  } catch {
    el.innerHTML = `<a class="${link}" href="/login.html">ENTRAR</a>`;
    return null;
  }
}
