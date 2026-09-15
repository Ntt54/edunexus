/**
 * markdown.ts — moteur de rendu markdown PARTAGÉ (web/vue).
 *
 * Unification des deux renderers maison (LessonView + TutorView) en un seul
 * module superset, consommé par les deux vues via un simple wrapper.
 *
 * SÉCURITÉ (assumé par le projet) :
 *  - Tout passage par `v-html` : chaque morceau de texte non-fiable est échappé
 *    (`escHtml`) AVANT toute injection ; on ne ré-injecte que nos propres balises.
 *  - Les liens passent par `safeHref` : schémas explicites limités à http(s),
 *    `javascript:` / `data:` / `vbscript:` REFUSÉS (rendus en texte brut).
 *  - Les images ne passent QUE par http(s) (jamais data:/javascript:), sinon
 *    l'alt est rendu en texte.
 *  - Le HTML brut écrit dans le markdown reste ÉCHAPPÉ (affiché en texte).
 *  - KaTeX : `trust: false` par défaut (`throwOnError: false`) → pas de
 *    `\href`/`\html*` actifs ; TeX invalide rendu en `.katex-error`.
 *
 * COUVERTURE :
 *  - Titres #→###### (`.md-h md-hN`), gras/italique/combiné, barré,
 *    code inline, blocs ``` (`.md-codeblock` + bouton copier `data-copy="code"`),
 *    listes - * + • et ordonnées 1. / 1) avec SOUS-listes (indentation 2/4),
 *    listes de tâches `- [ ]` / `- [x]` (checkbox désactivées stylées),
 *    citations > groupées (>> imbriquées), tableaux GFM alignés
 *    (`.md-tablewrap` + copie `data-copy="table"` + scroll horizontal),
 *    `---`/`***`/`___`, liens `[t](u)` / avec titre / `<https://…>` auto,
 *    images http(s), échappement `\#` `\*` …, retour ligne forcé `  ` → `<br>`,
 *    maths KaTeX : blocs `$$...$$`/`\[...\]` (displayMode), inline `$...$`/`\(...\)`.
 *
 * KaTeX est importé STATIQUEMENT : `renderToString` est synchrone, ce qui
 * s'intègre naturellement au rendu v-html ; les polices woff2 sont bundlées
 * par Vite. (La contrainte « zéro dépendance » vise `tutor/` Python, pas `web/`.)
 */

import katex from "katex";
import "katex/dist/katex.min.css";

/* ── API publique ─────────────────────────────────────────────── */

export interface MarkdownLabels {
  /** Libellé du bouton « copier » (i18n vue : t("lesson.copy")). */
  copy: string;
  /** Libellé du titre de tableau (i18n vue : t("lesson.table")). */
  table: string;
}

export interface MarkdownOptions {
  /**
   * Coloration syntaxique des blocs ```. Reçoit le code BRUT (non échappé,
   * avec sauts de ligne) + le langage assaini, et doit renvoyer du HTML sûr
   * (échappé). Par défaut : highlightCode (regex maison, zéro lib).
   */
  highlight?: (code: string, lang: string) => string;
  /** Libellés i18n (par défaut : français). */
  labels?: MarkdownLabels;
  /** Active le rendu KaTeX (défaut : true). false ⇒ délimitateurs en texte. */
  math?: boolean;
}

/* ── Échappement / URLs ───────────────────────────────────────── */

export function escHtml(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** URL acceptée pour href : relatif (sans schéma) ou http(s) explicite.
 *  REFUSE javascript: / data: / vbscript: / … (rendu en texte seul). */
export function safeHref(raw: string): string | null {
  const u = String(raw || "").trim();
  if (!u || /[\s<>]/.test(u)) return null;
  const m = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.exec(u);
  if (m) {
    const scheme = m[0].toLowerCase();
    if (scheme !== "http:" && scheme !== "https:") return null;
  }
  return u;
}

/* ── Coloration syntaxique par défaut (ex-LessonView, zéro lib) ──
 * NULLE confiance au contenu : on tokenize le code BRUT, chaque morceau est
 * échappé AVANT d'être enveloppé. Les mots/nombres sont cherchés hors-entités
 * pour ne jamais corrompre un échappement. */

const PY_KEYWORDS = new Set(
  ("False None True and as assert async await break class continue def del " +
    "elif else except finally for from global if import in is lambda nonlocal " +
    "not or pass raise return try while with yield match case").split(" "),
);
const PY_BUILTINS = new Set(
  ("print len range str int float bool list dict set tuple open enumerate zip " +
    "map filter sorted sum min max abs round isinstance type input").split(" "),
);

function highlightWords(chunk: string, isPy: boolean): string {
  return escHtml(chunk)
    .split(/(&[a-zA-Z]+;|&#[0-9]+;)/g)
    .map((part, k) => {
      if (k % 2 === 1) return part; // entité d'échappement : intacte
      return part.split(/(\b\d+(?:\.\d+)?\b)/g).map((p, j) => {
        if (j % 2 === 1) return `<span class="md-tok-num">${p}</span>`;
        if (!isPy) return p;
        return p.replace(/\b([A-Za-z_]\w*)(\()?/g, (whole, w: string, paren: string) => {
          if (PY_KEYWORDS.has(w)) return `<span class="md-tok-kw">${w}</span>${paren || ""}`;
          if (paren) return `<span class="md-tok-${PY_BUILTINS.has(w) ? "bi" : "fn"}">${w}</span>(`;
          return whole;
        });
      }).join("");
    })
    .join("");
}

export function highlightCode(code: string, lang: string): string {
  const isPy = /^(python|py)$/.test(lang);
  const re = isPy
    ? /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/g
    : /((?:\/\/|#)[^\n]*)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\\n])*`)/g;
  let out = "";
  let last = 0;
  const flushPlain = (chunk: string) => { out += highlightWords(chunk, isPy); };
  let m: RegExpExecArray | null;
  while ((m = re.exec(code)) !== null) {
    if (!m[0].length) { re.lastIndex++; continue; }
    flushPlain(code.slice(last, m.index));
    if (m[1] !== undefined) out += `<span class="md-tok-com">${escHtml(m[1])}</span>`;
    else out += `<span class="md-tok-str">${escHtml(m[2])}</span>`;
    last = m.index + m[0].length;
  }
  flushPlain(code.slice(last));
  return out;
}

/* ── Maths (KaTeX) ──────────────────────────────────────────────
 * throwOnError:false ⇒ KaTeX ne jette JAMAIS sur du TeX invalide (il rend la
 * source en .katex-error rouge). Option trust non renseignée ⇒ false par
 * défaut : \href / \html* désactivés. Le try/catch reste en ceinture. */

function renderMath(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex ?? "", { throwOnError: false, displayMode });
  } catch {
    return `<span class="katex-error">${escHtml(tex)}</span>`;
  }
}

/* ── Rendu inline ───────────────────────────────────────────────
 * Tokenizer unique sur le texte BRUT : on extrait d'abord code inline,
 * maths ($..$ / \(..\) / \[..\]), et échappements (\x), remplacés par des
 * placeholders private-use (jamais de maths/code dans du code, jamais de
 * markdown dans du code), puis on échappe le reste, on applique links/images/
 * gras/italique/barré sur le texte échappé, enfin on restaure les tokens. */

const PH = "\uE000";
const PH_END = "\uE001";

type Token =
  | { k: "code"; v: string }
  | { k: "math"; v: string; d: boolean }
  | { k: "esc"; v: string };

const INLINE_TOKEN = /(`[^`\n]+`)|(\$[^\s$](?:[^$\n]*?[^\s$])?\$)|(\\\([\s\S]*?\\\))|(\\\[[\s\S]*?\\\])|(\\([\\`*_\[\]()#+\-.!>~|$]))/g;
const INLINE_TOKEN_NO_MATH = /(`[^`\n]+`)|(\\([\\`*_\[\]()#+\-.!>~|$]))/g;

function tokenizeInline(seg: string, math: boolean): { marked: string; tokens: Token[] } {
  const tokens: Token[] = [];
  const re = math ? INLINE_TOKEN : INLINE_TOKEN_NO_MATH;
  // NB : regex avec /g réinitialisée systématiquement (lastIndex).
  re.lastIndex = 0;
  const marked = seg.replace(re, (whole, code: string | undefined, dollar: string | undefined, paren: string | undefined, bracket: string | undefined, esc: string | undefined, escChar: string | undefined): string => {
    let tok: Token;
    if (code !== undefined) tok = { k: "code", v: code.slice(1, -1) };
    else if (dollar !== undefined) tok = { k: "math", v: dollar.slice(1, -1), d: false };
    else if (paren !== undefined) tok = { k: "math", v: paren.slice(2, -2), d: false };
    else if (bracket !== undefined) tok = { k: "math", v: bracket.slice(2, -2), d: true };
    else tok = { k: "esc", v: escChar ?? whole.slice(1) };
    tokens.push(tok);
    return PH + (tokens.length - 1).toString(36) + PH_END;
  });
  return { marked, tokens };
}

/** Markdown inline sur du texte DÉJÀ échappé (placeholders préservés). */
function applyInline(s: string): string {
  // Images : http(s) UNIQUEMENT (data:/javascript: refusées → texte alt).
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+(&quot;[^&)]*?&quot;))?\)/g,
    (whole, alt: string, url: string, title: string | undefined) => {
      if (!/^https?:\/\//i.test(url)) return whole;
      const t = title ? ` title="${title.slice(6, -6)}"` : "";
      return `<img class="md-img" src="${url}" alt="${alt}" loading="lazy"${t}>`;
    });
  // Liens [t](u) et [t](u "Titre") ; safeHref sur l'URL échappée.
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+(&quot;[^&)]*?&quot;))?\)/g,
    (whole, text: string, url: string, title: string | undefined) => {
      const href = safeHref(url);
      if (!href) return text;
      const ext = /^https?:\/\//i.test(href);
      const t = title ? ` title="${title.slice(6, -6)}"` : "";
      return `<a class="md-link" href="${href}"${ext ? ' target="_blank" rel="noopener"' : ""}${t}>${text}</a>`;
    });
  // URLs automatiques <https://…> (forme échappée par escHtml).
  s = s.replace(/&lt;(https?:\/\/[^<>\s]*?)&gt;/g, (whole, url: string) => {
    const href = safeHref(url);
    return href ? `<a class="md-link" href="${href}" target="_blank" rel="noopener">${href}</a>` : whole;
  });
  // Gras / italique / combiné / barré (ordre important).
  s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^\w*_])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w_])(_)([^_\n]+?)_(?!\w)/g, "$1<em>$3</em>");
  s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  return s;
}

const ESC_RESTORE: Record<string, string> = { ">": "&gt;" };

function restoreInline(marked: string, tokens: Token[]): string {
  const re = new RegExp(PH + "([0-9a-z]+)" + PH_END, "g");
  return marked.replace(re, (_m, idx: string) => {
    const tok = tokens[parseInt(idx, 36)];
    if (!tok) return "";
    if (tok.k === "code") return `<code class="md-code-inline">${escHtml(tok.v)}</code>`;
    if (tok.k === "esc") return ESC_RESTORE[tok.v] ?? tok.v;
    return `<span class="md-math-inline">${renderMath(tok.v, tok.d)}</span>`;
  });
}

/** Rendu inline d'une ligne (utilisé par les paragraphes, cellules, titres,
 *  items de liste, citations). Sûr : seul du HTML produit par ce module
 *  est injecté, tout texte passe par escHtml. */
export function renderInline(seg: string, math = true): string {
  const src = String(seg ?? "");
  const { marked, tokens } = tokenizeInline(src, math);
  const escaped = applyInline(escHtml(marked));
  const restored = restoreInline(escaped, tokens);
  // Retour ligne forcé : deux espaces en fin de ligne ⇒ <br>.
  return restored.replace(/[ \t]{2,}$/, "<br>");
}

/* ── Listes (nostrées réelles, sous-listes, tâches) ───────────── */

interface ListItem {
  tag: "ul" | "ol";
  depth: number;
  start: number | null;
  content: string;
  checked: boolean | null;
  continuations: string[];
}

const LIST_MARKER = /^(\s*)([-*+•]|\d+[.)])\s+(.*)$/;
const TASK_MARK = /^\[([ xX])\]\s*(.*)$/;

function indentOf(line: string): number {
  return line.replace(/^(\s*).*$/, "$1").replace(/\t/g, "  ").length;
}

function parseListLine(line: string, baseIndent: number): ListItem | null {
  const m = LIST_MARKER.exec(line);
  if (!m) return null;
  const indent = m[1].replace(/\t/g, "  ").length;
  const marker = m[2];
  const rest = m[3];
  const isOl = /^\d/.test(marker);
  const tag: "ul" | "ol" = isOl ? "ol" : "ul";
  const start = isOl ? parseInt(marker, 10) : null;
  const task = TASK_MARK.exec(rest);
  let checked: boolean | null = null;
  let content = rest;
  if (task) { checked = task[1].toLowerCase() === "x"; content = task[2]; }
  return {
    tag,
    depth: Math.max(0, Math.floor((indent - baseIndent) / 2)),
    start,
    content,
    checked,
    continuations: [],
  };
}

function parseListBlock(lines: string[], start: number, math: boolean): { html: string; next: number } {
  const items: ListItem[] = [];
  const base = indentOf(lines[start]);
  let j = start;
  for (; j < lines.length; j++) {
    const line = lines[j];
    const it = parseListLine(line, base);
    if (it) { items.push(it); continue; }
    // Continuation d'item : ligne indentée non-marqueur (paragraphe de l'item).
    const last = items[items.length - 1];
    const trimmed = line.trim();
    if (last && trimmed && /^[ \t]/.test(line)) {
      last.continuations.push(trimmed);
      continue;
    }
    break;
  }
  return { html: buildListHtml(items, math), next: j };
}

function buildListHtml(items: ListItem[], math: boolean): string {
  type Open = { tag: "ul" | "ol"; depth: number; itemOpen: boolean };
  const stack: Open[] = [];
  let html = "";
  const closeTop = () => {
    const top = stack.pop();
    if (!top) return;
    if (top.itemOpen) html += "</li>";
    html += `</${top.tag}>`;
  };
  for (const it of items) {
    while (stack.length && stack[stack.length - 1].depth > it.depth) closeTop();
    const top0 = stack[stack.length - 1];
    if (top0 && top0.depth === it.depth && top0.tag !== it.tag) closeTop();
    while (stack.length <= it.depth) {
      const startAttr = it.tag === "ol" && it.start != null && it.start > 1 ? ` start="${it.start}"` : "";
      html += `<${it.tag} class="md-list"${startAttr}>`;
      stack.push({ tag: it.tag, depth: stack.length, itemOpen: false });
    }
    const cur = stack[stack.length - 1];
    if (cur.itemOpen) html += "</li>"; // ferme le précédent frère, on reste dans la liste
    const cb = it.checked === null
      ? ""
      : `<input type="checkbox" class="md-check"${it.checked ? " checked" : ""} disabled>`;
    html += `<li${it.checked !== null ? ' class="md-task"' : ""}>${cb}${renderInline(it.content, math)}`;
    for (const cont of it.continuations) {
      html += `<div class="tutor-para">${renderInline(cont, math)}</div>`;
    }
    cur.itemOpen = true;
  }
  while (stack.length) closeTop();
  return html;
}

/* ── Rendu par blocs ──────────────────────────────────────────── */

function renderMathBlock(tex: string): string {
  return `<div class="md-math-block">${renderMath(tex, true)}</div>`;
}

function renderCodeBlock(
  code: string,
  lang: string,
  highlight: (c: string, l: string) => string,
  labels: MarkdownLabels,
): string {
  return `<div class="md-codeblock"><div class="md-codehead"><span class="md-codelang">${escHtml(lang)}</span>` +
    `<button type="button" class="md-copybtn" data-copy="code">${escHtml(labels.copy)}</button></div>` +
    `<pre class="md-code"><code class="language-${lang}" data-lang="${lang}">${highlight(code, lang)}</code></pre></div>`;
}

function splitTableRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

const SPACER = '<div style="height:6px"></div>';

/* ── API principale ───────────────────────────────────────────── */

/**
 * Rendu markdown complet (fiche utilisateur + LaTeX).
 * @param raw     contenu markdown non-fiable (échapé ici).
 * @param options highlight (coloration), labels (i18n), math (KaTeX on/off).
 */
export function renderMarkdown(raw: string, options: MarkdownOptions = {}): string {
  const {
    highlight = highlightCode,
    labels = { copy: "Copier", table: "Table" },
    math: mathEnabled = true,
  } = options;
  const lines = String(raw || "").split("\n");
  let out = "";
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    const tl = ln.trim();

    // Blocs clôturés ```lang (non fermé ⇒ tout le reste en code) :
    // en-tête (langage + copier) + coloration sur tokens échappés.
    const fence = /^```(\w*)\s*$/.exec(tl);
    if (fence) {
      const lang = (fence[1] || "text").toLowerCase().replace(/[^a-z0-9+-]/g, "") || "text";
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
      if (i < lines.length) i++;
      out += renderCodeBlock(buf.join("\n"), lang, highlight, labels);
      continue;
    }

    // Maths : blocs display $$…$$ (multi-lignes ou une ligne) et \[…\].
    if (mathEnabled) {
      if (/^\$\$\s*$/.test(tl)) {
        const buf: string[] = [];
        i++;
        while (i < lines.length && !/^\$\$\s*$/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
        if (i < lines.length) i++;
        out += renderMathBlock(buf.join("\n"));
        continue;
      }
      const mSingle = /^\$\$(.+?)\$\$\s*$/.exec(tl);
      if (mSingle) { out += renderMathBlock(mSingle[1]); i++; continue; }
      // \[ … \] multiligne : ouverture/fermeture sur lignes dédiées.
      if (/^\\\[\s*$/.test(tl)) {
        const buf: string[] = [];
        i++;
        while (i < lines.length && !/^\\\]\s*$/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
        if (i < lines.length) i++;
        out += renderMathBlock(buf.join("\n"));
        continue;
      }
      const mBrk = /^\\\[(.+)\\\]\s*$/.exec(tl);
      if (mBrk) { out += renderMathBlock(mBrk[1]); i++; continue; }
    }

    // Citations « > » groupées ; « >> » = imbrication simple.
    if (/^(\s*>)+/.test(ln)) {
      const buf: Array<{ depth: number; text: string }> = [];
      while (i < lines.length) {
        const m = /^(\s*>+)\s?(.*)$/.exec(lines[i]);
        if (!m) break;
        buf.push({ depth: m[1].trim().length, text: m[2] });
        i++;
      }
      const opens: number[] = [];
      let q = "";
      const closeTo = (d: number) => { while (opens.length > d) { opens.pop(); q += "</blockquote>"; } };
      for (const row of buf) {
        closeTo(row.depth);
        while (opens.length < row.depth) { opens.push(1); q += '<blockquote class="md-quote">'; }
        q += row.text.trim() ? `<div>${renderInline(row.text, mathEnabled)}</div>` : SPACER;
      }
      closeTo(0);
      out += q;
      continue;
    }

    // Tableaux GFM : en-tête + séparateur |---|:---:| puis lignes.
    if (tl.includes("|") && i + 1 < lines.length) {
      const sep = splitTableRow(lines[i + 1]);
      const head = splitTableRow(tl);
      const isSep = sep.length === head.length && sep.length > 0 &&
        sep.every((c) => /^:?-+:?$/.test(c));
      if (isSep) {
        const aligns = sep.map((c) =>
          c.startsWith(":") && c.endsWith(":") && c.length > 2 ? "center"
            : c.endsWith(":") ? "right" : "left");
        const alignAttr = (a: string) => (a === "left" ? "" : ` align="${a}"`);
        out += `<div class="md-tablewrap"><div class="md-tablebar"><span class="md-tabletitle">${escHtml(labels.table)}</span>` +
          `<button type="button" class="md-copybtn" data-copy="table">${escHtml(labels.copy)}</button></div>` +
          `<div class="md-tablescroll"><table class="md-table"><thead><tr>${head.map((c, k) =>
            `<th${alignAttr(aligns[k])}>${renderInline(c, mathEnabled)}</th>`).join("")}</tr></thead><tbody>`;
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const cells = splitTableRow(lines[i]);
          out += `<tr>${head.map((_, k) =>
            `<td${alignAttr(aligns[k])}>${renderInline(cells[k] ?? "", mathEnabled)}</td>`).join("")}</tr>`;
          i++;
        }
        out += `</tbody></table></div></div>`;
        continue;
      }
    }

    if (!tl) { out += SPACER; i++; continue; }

    // Titres #…###### (classes md-hN conservées).
    const hm = /^(#{1,6})\s+(.*)$/.exec(tl);
    if (hm) { out += `<div class="md-h md-h${hm[1].length}">${renderInline(hm[2], mathEnabled)}</div>`; i++; continue; }

    // Règle horizontale --- / *** / ___.
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(tl)) { out += '<hr class="md-hr">'; i++; continue; }

    // Listes (ordonnées / non ordonnées / tâches), imbrication réelle.
    if (parseListLine(ln, indentOf(ln))) {
      const block = parseListBlock(lines, i, mathEnabled);
      out += block.html;
      i = block.next;
      continue;
    }

    // Ligne simple / paragraphe (comportement actuel préservé).
    out += `<div class="tutor-para">${renderInline(ln, mathEnabled)}</div>`;
    i++;
  }
  return out;
}

/* ── Copie au clic (boutons nés dans le v-html) ─────────────────
 * Dépend du markup produit ici : md-copybtn[data-copy] dans
 * .md-codeblock / .md-tablewrap. Le texte est relu depuis le DOM
 * (innerText décode les entités ; la coloration ne fait qu'envelopper). */

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* repli ci-dessous */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** Délégation de clic unique par conteneur v-html (usage TutorView). */
export function handleMarkdownClick(e: Event, copiedLabel: string): void {
  const target = e.target as HTMLElement | null;
  const btn = target?.closest?.("[data-copy]") as HTMLElement | null;
  if (!btn) return;
  const kind = btn.dataset.copy;
  const root = btn.closest(".md-codeblock, .md-tablewrap");
  let text = "";
  if (kind === "code") {
    const code = root?.querySelector("code");
    if (code) text = (code as HTMLElement).innerText;
  } else if (kind === "table") {
    const table = root?.querySelector("table");
    if (table) {
      text = Array.from((table as HTMLTableElement).rows)
        .map((r) => Array.from(r.cells)
          .map((c) => (c as HTMLElement).innerText.replace(/\s+/g, " ").trim()).join("\t"))
        .join("\n");
    }
  }
  if (!text) return;
  const label = btn.textContent;
  void copyText(text).then((ok) => {
    if (!ok || !btn.isConnected) return;
    btn.textContent = copiedLabel;
    setTimeout(() => { if (btn.isConnected) btn.textContent = label; }, 1500);
  });
}