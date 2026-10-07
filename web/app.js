/* Ecolyra — plataforma (aba única). Tudo conversa com o ecolyra.pyw pela API local (/api/...). */
'use strict';

const CFG = window.ECOLYRA;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const vista = $('#vista');
const IDIOMAS = { pt: 'Português', en: 'English', es: 'Español', fr: 'Français', it: 'Italiano', de: 'Deutsch', ja: '日本語', ko: '한국어' };
const ESTILOS = { vidro: 'Vidro', cartao: 'Cartão', capa: 'Capa', minimal: 'Minimal', degrade: 'Degradê' };
const ICONE = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 4h3v16H7zM14 4h3v16h-3z"/></svg>',
  video: '<svg viewBox="0 0 24 24"><rect x="2" y="5" width="15" height="14" rx="2"/><path d="m17 10 5-3v10l-5-3"/></svg>',
  post: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="12" cy="12" r="4"/></svg>',
  copiar: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>',
  baixar: '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>',
  pasta: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  buscar: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  fone: '<svg viewBox="0 0 24 24"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1v-6h3zM3 19a2 2 0 0 0 2 2h1v-6H3z"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
};

// ---------------- utilidades ----------------
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const mmss = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const seg = (s) => (s == null ? '' : mmss(s));
const num = (v) => (Math.round(v * 100) / 100).toFixed(2).replace('.', ',');
const enc = encodeURIComponent;
const normalizar = (s) => String(s || '').toLowerCase().normalize('NFKD').replace(/[^a-z0-9]/g, '');

async function api(rota, corpo) {
  const op = { headers: { 'X-Token': CFG.token } };
  if (corpo !== undefined) {
    op.method = 'POST';
    op.headers['Content-Type'] = 'application/json';
    op.body = JSON.stringify(corpo);
  }
  const r = await fetch('/api/' + rota, op);
  if (r.status === 403) { // o Ecolyra reiniciou (atualização): recarrega para pegar a sessão nova
    setTimeout(() => location.reload(), 300);
    throw new Error('reconectando');
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.erro || `erro ${r.status}`);
  return j;
}

let avisoTimer;
function aviso(texto, erro = false, tempo = 2600) {
  const a = $('#aviso');
  a.textContent = texto;
  a.classList.toggle('erro', erro);
  a.classList.add('mostrar');
  clearTimeout(avisoTimer);
  avisoTimer = setTimeout(() => a.classList.remove('mostrar'), tempo);
}

// cor média da capa → tinge o fundo
const cores = new Map();
function corDaCapa(url) {
  if (!url) return;
  if (cores.has(url)) return document.documentElement.style.setProperty('--capa', cores.get(url));
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 12;
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(img, 0, 0, 12, 12);
      const d = g.getImageData(0, 0, 12, 12).data;
      let r = 0, gg = 0, b = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) {
        const sat = Math.max(d[i], d[i + 1], d[i + 2]) - Math.min(d[i], d[i + 1], d[i + 2]);
        const w = 1 + sat / 40;
        r += d[i] * w; gg += d[i + 1] * w; b += d[i + 2] * w; n += w;
      }
      const k = 0.85; // um pouco mais escuro, pra letra branca aparecer
      const v = `${Math.round(r / n * k)}, ${Math.round(gg / n * k)}, ${Math.round(b / n * k)}`;
      cores.set(url, v);
      document.documentElement.style.setProperty('--capa', v);
    } catch (e) { /* imagem sem CORS: fica a cor padrão */ }
  };
  img.src = url;
}

// ---------------- estado ----------------
let E = { ativo: false, tocando: false, pos: 0, duracao: 0, offset: 0, status: '', letra_v: '' };
let recebidoEm = performance.now();
let LETRA = { linhas: [], trad: [], status: '', v: '' };        // letra da música tocando agora
const musicas = new Map();                                         // id → faixa (busca, histórico)
let ultimaBusca = { q: '', r: null };
let POST = null;                                                   // estado do estúdio de post
let rotaAtual = '';

// ---------------- player interno (YouTube) ----------------
const P = { yt: null, pronto: false, faixa: null, modo: 'audio', cands: [], ci: 0, video: null, ver: false, grande: false, fila: [], fi: -1, pendente: null };

window.onYouTubeIframeAPIReady = () => {
  P.yt = new YT.Player('yt', {
    width: '100%', height: '100%',
    playerVars: { autoplay: 1, rel: 0, modestbranding: 1, playsinline: 1, origin: location.origin, iv_load_policy: 3 },
    events: {
      onReady: () => { P.pronto = true; if (P.pendente) { const v = P.pendente; P.pendente = null; P.yt.loadVideoById(v); } },
      onStateChange: (e) => { if (e.data === 0) proxima(); atualizarBotaoPlay(); },
      onError: (e) => { // vídeo bloqueado
        // para embed (101/150) ou removido: tenta o próximo
        console.warn('[Ecolyra] erro do YouTube', e.data, P.video && P.video.id);
        P.ci += 1;
        if (P.ci < P.cands.length) { aviso('Esse vídeo não deixa tocar aqui, tentando outro...'); carregarVideo(); }
        else aviso('Não achei um vídeo que toque aqui dentro 😕', true);
      },
    },
  });
};

function ytTocando() { return P.pronto && P.faixa && P.yt.getPlayerState && P.yt.getPlayerState() === 1; }
function ytTempo() { return P.pronto && P.yt.getCurrentTime ? P.yt.getCurrentTime() || 0 : 0; }

async function letraDaFaixa(f, idioma) {
  idioma = idioma || CONFIG.idioma || 'pt';
  if (f._letra && f._letra_idioma === idioma) return f._letra;
  f._letra = await api('letra', { ...f, idioma, abrir: true });
  f._letra_idioma = idioma;
  return f._letra;
}

async function tocar(f, modo = 'audio', fila = null) {
  if (!f) return;
  musicas.set(f.id, f);
  if (fila) { P.fila = fila; P.fi = fila.findIndex((x) => x.id === f.id); }
  P.faixa = f; P.modo = modo; P.cands = []; P.ci = 0;
  aviso(modo === 'clipe' ? 'Procurando o clipe...' : 'Procurando a música...');
  try {
    const l = await letraDaFaixa(f).catch(() => null);
    await api('tocar', { titulo: f.titulo, artista: f.artista, lrc: l?.lrc || '', modo });
    if (E.tocando && !E.interno) api('comando', { cmd: 'pp' }).catch(() => {}); // pausa o Spotify/YouTube de fora
    let cands = await api(`videos?titulo=${enc(f.titulo)}&artista=${enc(f.artista)}&dur=${f.duracao || 0}&tipo=${modo}`);
    if (modo === 'clipe' && f.video_id) cands = [{ id: f.video_id, titulo: f.titulo_completo || f.titulo }, ...cands.filter((v) => v.id !== f.video_id)];
    if (modo === 'audio' && f.video_id && !cands.length) cands = [{ id: f.video_id, titulo: f.titulo }];
    if (!cands.length) { aviso('Não achei essa música no YouTube 😕', true); return; }
    P.cands = cands;
    carregarVideo();
  } catch (e) { aviso('Erro ao tocar: ' + e.message, true); }
}

function carregarVideo() {
  const v = P.cands[P.ci];
  P.video = v;
  $('#palcoTitulo').textContent = v.titulo || '';
  if (P.pronto) P.yt.loadVideoById(v.id); else P.pendente = v.id;
  posicionarPalco();
  if (rotaAtual.startsWith('musica')) desenharInfoVideo();
}

// "tocar <nome ou link>" vindo de fora (overlay/bandeja): resolve e toca aqui dentro
async function tocarPorBusca(arg) {
  const q = typeof arg === 'string' ? arg : arg?.q;
  const modo = (typeof arg === 'object' && arg?.modo) || 'audio';
  if (!q) return;
  const r = await api('buscar?q=' + enc(q)).catch(() => null);
  const f = r?.principal || r?.resultados?.[0];
  if (!f) return aviso('Não achei: ' + q, true);
  r.resultados.forEach((x) => musicas.set(x.id, x));
  location.hash = '#/musica/' + enc(f.id);
  tocar(f, modo, r.resultados);
}

function trocarVideo() {
  if (!P.cands.length) return;
  P.ci = (P.ci + 1) % P.cands.length;
  carregarVideo();
}

function proxima() {
  if (P.fila.length && P.fi + 1 < P.fila.length) { P.fi += 1; tocar(P.fila[P.fi], P.modo); }
}
function anterior() {
  if (ytTempo() > 3 || P.fi <= 0) return P.yt.seekTo(0, true);
  P.fi -= 1; tocar(P.fila[P.fi], P.modo);
}

function comandoLocal(cmd, arg) {
  if (!P.pronto || !P.faixa) return;
  if (cmd === 'pp') ytTocando() ? P.yt.pauseVideo() : P.yt.playVideo();
  else if (cmd === 'proxima') proxima();
  else if (cmd === 'anterior') anterior();
  else if (cmd === 'seek') P.yt.seekTo(Number(arg) || 0, true);
  setTimeout(atualizarBotaoPlay, 150);
}

function comando(cmd, arg) {
  if (P.faixa && (E.interno || !E.ativo)) return comandoLocal(cmd, arg);
  api('comando', { cmd, arg }).catch((e) => aviso(e.message, true));
}

function posicionarPalco() {
  const palco = $('#palco');
  const mostrar = P.faixa && (P.modo === 'clipe' || P.ver);
  palco.className = 'palco';
  palco.style.cssText = '';
  if (!P.faixa) return palco.classList.add('oculto');
  if (P.grande) return palco.classList.add('grande');
  const vaga = $('.vaga-video');
  if (vaga && (mostrar || vaga.dataset.sempre)) {
    const r = vaga.getBoundingClientRect();
    if (r.width > 10 && r.bottom > 0 && r.top < innerHeight) {
      Object.assign(palco.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
      return;
    }
  }
  palco.classList.add(mostrar ? 'mini' : 'oculto');
}
addEventListener('resize', posicionarPalco);
vista.addEventListener('scroll', posicionarPalco, { passive: true });
$('#palcoExpandir').onclick = () => { P.grande = !P.grande; posicionarPalco(); };
$('#pVideo').onclick = () => {
  if (!P.faixa) { // nada tocando aqui dentro: abre o clipe da música que toca lá fora
    if (E.titulo) return tocar({ id: 'agora' + normalizar(E.titulo + E.artista), titulo: E.titulo, artista: E.artista, duracao: E.duracao }, 'clipe');
    return aviso('Busque uma música para ver o vídeo');
  }
  if (P.grande) { P.grande = false; P.ver = false; }
  else if (P.ver || P.modo === 'clipe') P.grande = true;
  else P.ver = true;
  posicionarPalco();
  $('#pVideo').classList.toggle('ativo', P.ver || P.grande);
};

// ---------------- sincronização com o Ecolyra ----------------
let falhas = 0;
async function sincronizar() {
  let corpo = {};
  if (P.faixa && P.pronto) {
    const d = P.yt.getDuration ? P.yt.getDuration() : 0;
    corpo.player = {
      titulo: P.faixa.titulo, artista: P.faixa.artista, duracao: d || P.faixa.duracao || 0, pos: ytTempo(),
      tocando: ytTocando(), video_titulo: (P.yt.getVideoData && P.yt.getVideoData().title) || '', modo: P.modo,
      capa: P.faixa.capa_hd || P.faixa.capa || '',
    };
  }
  try {
    const r = await api('sync', corpo);
    falhas = 0;
    E = r; recebidoEm = performance.now();
    for (const c of r.cmds || []) {
      if (c.cmd === 'navegar') location.hash = '#' + (c.arg || '/agora');
      else if (c.cmd === 'tocar') tocarPorBusca(c.arg);
      else comandoLocal(c.cmd, c.arg);
    }
    if (E.letra_v !== LETRA.v) LETRA = await api('agora/letra');
    atualizarPlayer();
  } catch (e) {
    if (++falhas === 4) aviso('Reconectando ao Ecolyra...', true, 4000);
  }
  setTimeout(sincronizar, 400);
}

function posAgora() {
  if (E.interno && P.pronto && P.faixa) return ytTempo(); // esta janela é que está tocando
  return (E.pos || 0) + (E.tocando ? (performance.now() - recebidoEm) / 1000 : 0);
}

let capaV = -1;
function atualizarPlayer() {
  const ativo = E.ativo || P.faixa;
  $('#pTitulo').textContent = E.ativo ? E.titulo : (P.faixa ? P.faixa.titulo : 'Nada tocando');
  $('#pArtista').textContent = E.ativo ? `${E.artista || ''}${E.fonte ? '  ·  ' + E.fonte : ''}` : 'Busque uma música ou toque no Spotify, YouTube...';
  if (E.capa_v !== capaV) {
    capaV = E.capa_v;
    const src = E.interno && P.faixa?.capa ? P.faixa.capa : (E.ativo ? `/api/agora/capa?t=${enc(CFG.token)}&v=${E.capa_v}` : 'icone.png');
    const img = $('#pCapa');
    img.onerror = () => { img.onerror = null; img.src = 'icone.png'; };
    img.src = src;
  }
  $('#fonteAgora').innerHTML = E.ativo ? `Tocando no <b>${esc(E.fonte || 'player')}</b>` : '';
  const s = $('#pSinc');
  s.textContent = (E.auto != null ? 'auto ' : '') + (E.offset ? (E.offset > 0 ? '+' : '') + num(E.offset) + 's' : '0,00s');
  s.classList.toggle('auto', E.auto != null);
  atualizarBotaoPlay();
  $('#player').style.opacity = ativo ? 1 : .85;
}

function atualizarBotaoPlay() {
  const tocando = E.interno && P.faixa ? ytTocando() : E.tocando;
  $('#pPlay').innerHTML = tocando ? ICONE.pause : ICONE.play;
}

$$('.p-botoes [data-cmd]').forEach((b) => (b.onclick = () => comando(b.dataset.cmd)));
$$('[data-ajuste]').forEach((b) => (b.onclick = () => api('ajuste', { delta: Number(b.dataset.ajuste) })));
$('#pSinc').onclick = () => api('ajuste', { delta: null });
$('#pSinc').title = 'Clique para zerar o ajuste manual';
$('#pBarra').onclick = (e) => {
  const dur = E.duracao || (P.pronto ? P.yt.getDuration() : 0);
  if (!dur) return;
  const r = e.currentTarget.getBoundingClientRect();
  comando('seek', Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur);
};
$('#pPost').onclick = () => postDoQueToca();
$('#credito').onclick = () => (location.hash = '#/sobre');

// quadro a quadro: barra de tempo + letra destacada
function quadro() {
  const pos = posAgora();
  const dur = E.duracao || 0;
  $('#pPos').textContent = mmss(pos);
  $('#pDur').textContent = mmss(dur);
  $('#pFeito').style.width = dur ? Math.min(100, pos / dur * 100) + '%' : '0';
  if (rotaAtual === 'agora') marcarKaraoke(pos + (E.offset || 0));
  if (rotaAtual.startsWith('musica')) marcarMusica();
  requestAnimationFrame(quadro);
}

// ---------------- rotas ----------------
const ROTAS = { agora: verAgora, buscar: verBuscar, musica: verMusica, post: verPost, historico: verHistorico, config: verConfig, sobre: verSobre };

function navegar() {
  const h = location.hash.replace(/^#\/?/, '') || 'agora';
  const [nome, ...resto] = h.split('/');
  rotaAtual = h;
  $$('.menu a').forEach((a) => a.classList.toggle('ativo', a.dataset.rota === nome));
  if (P.grande) P.grande = false;
  vista.scrollTo({ top: 0, behavior: 'instant' });
  (ROTAS[nome] || verAgora)(decodeURIComponent(resto.join('/')));
  requestAnimationFrame(posicionarPalco);
}
addEventListener('hashchange', navegar);

// ---------------- Tocando agora (karaokê) ----------------
let karaokeV = '', karaokeIdx = -2, mexeuEm = 0;
function verAgora() {
  karaokeV = ''; karaokeIdx = -2;
  const comVideo = P.faixa && (P.modo === 'clipe' || P.ver);
  vista.innerHTML = `
    <section class="agora">
      <div class="agora-fundo"></div>
      <header class="agora-topo">
        <img id="agCapa" src="icone.png" alt="">
        <div>
          <div class="rotulo" id="agRotulo">Tocando agora</div>
          <h1 id="agTitulo">—</h1>
          <div class="sub" id="agArtista" style="margin:0"></div>
        </div>
        <div class="acoes-agora">
          <button class="botao fantasma" id="agClipe">${ICONE.video} Ver clipe aqui</button>
          <button class="botao principal" id="agPost">${ICONE.post} Criar post</button>
        </div>
      </header>
      <div class="agora-corpo ${comVideo ? 'com-video' : ''}">
        <div class="karaoke" id="karaoke"></div>
        ${comVideo ? '<div class="lado-video"><div class="vaga-video"></div></div>' : ''}
      </div>
    </section>`;
  $('#agPost').onclick = postDoQueToca;
  $('#agClipe').onclick = () => {
    if (!E.titulo) return aviso('Nada tocando agora');
    if (P.faixa && normalizar(P.faixa.titulo) === normalizar(E.titulo)) { P.ver = true; return verAgora(); }
    tocar({ id: 'agora' + normalizar(E.titulo + E.artista), titulo: E.titulo, artista: E.artista, duracao: E.duracao, capa_hd: P.faixa?.capa_hd }, 'clipe').then(() => setTimeout(verAgora, 300));
  };
  $('#karaoke').addEventListener('wheel', () => (mexeuEm = performance.now()), { passive: true });
  setTimeout(posicionarPalco, 50);
}

function marcarKaraoke(t) {
  const k = $('#karaoke');
  if (!k) return;
  const topo = `${E.titulo || ''}|${E.artista || ''}`;
  if ($('#agTitulo').dataset.k !== topo) {
    $('#agTitulo').dataset.k = topo;
    $('#agTitulo').textContent = E.titulo || 'Nada tocando';
    $('#agArtista').textContent = E.artista || 'Toque uma música no Spotify, YouTube... ou busque aqui no Ecolyra.';
    $('#agRotulo').textContent = E.ativo ? `Tocando agora · ${E.fonte || ''}` : 'Ecolyra';
  }
  const capa = $('#pCapa').src;
  if ($('#agCapa').src !== capa) { $('#agCapa').src = capa; corDaCapa(capa); }
  const estado = !E.ativo ? 'parado' : !LETRA.linhas.length ? 'sem' : E.esperando ? 'esperando' : 'ok';
  const v = LETRA.v + estado + (estado !== 'ok' ? E.status : '');
  if (v !== karaokeV) {
    karaokeV = v; karaokeIdx = -2;
    if (estado === 'parado') k.innerHTML = `<div class="estado-letra">Toque uma música no Spotify, YouTube ou outro player ♪<br><br><a class="botao principal" href="#/buscar">${ICONE.buscar} Buscar uma música</a></div>`;
    else if (estado === 'sem') k.innerHTML = `<div class="estado-letra">${/Buscando|Identificando|Traduzindo|Reconheci/.test(E.status) ? '<span class="carregando"></span>' : ''}${esc(E.status || 'Sem letra')}</div>`;
    else if (estado === 'esperando') k.innerHTML = '<div class="estado-letra"><span class="carregando"></span>Escutando a música para sincronizar a letra...</div>';
    else {
      k.innerHTML = LETRA.linhas.map(([tempo, orig], i) => {
        const tr = LETRA.trad[i] || '';
        if (!orig) return `<div class="k instr" data-t="${tempo}">♪</div>`;
        const mostra = tr && normalizar(tr) !== normalizar(orig);
        return `<div class="k" data-t="${tempo}">${esc(tr || orig)}${mostra ? `<small>${esc(orig)}</small>` : ''}</div>`;
      }).join('');
      $$('.k', k).forEach((el) => (el.onclick = () => comando('seek', Math.max(0, Number(el.dataset.t) - (E.offset || 0)))));
    }
  }
  if (estado !== 'ok') return;
  let idx = -1;
  for (let i = 0; i < LETRA.linhas.length; i++) { if (LETRA.linhas[i][0] <= t) idx = i; else break; }
  if (idx === karaokeIdx) return;
  karaokeIdx = idx;
  $$('.k', k).forEach((el, i) => { el.classList.toggle('atual', i === idx); el.classList.toggle('passado', i < idx); });
  const el = k.children[Math.max(0, idx)];
  if (el && performance.now() - mexeuEm > 5000) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

// ---------------- Buscar ----------------
function cartao(f, destaque = false) {
  return `
    <article class="cartao-musica ${destaque ? 'destaque' : ''}" data-id="${esc(f.id)}" tabindex="0">
      <div class="capa"><img loading="lazy" src="${esc(f.capa || f.capa_hd || 'icone.png')}" alt="" onerror="this.src='icone.png'">
        <button class="tocar-rapido" data-tocar="${esc(f.id)}" title="Ouvir aqui">${ICONE.play}</button></div>
      <div style="min-width:0">
        ${destaque ? `<div class="rotulo">Do seu link · ${esc(f.origem || f.fonte || '')}</div>` : ''}
        <strong>${esc(f.titulo)}</strong><br>
        <span>${esc(f.artista)}${f.duracao ? ' · ' + mmss(f.duracao) : ''}</span>
      </div>
    </article>`;
}

function ligarCartoes(el, lista) {
  lista.forEach((f) => musicas.set(f.id, f));
  $$('.cartao-musica', el).forEach((c) => {
    const abrir = () => (location.hash = '#/musica/' + enc(c.dataset.id));
    c.onclick = (e) => { if (!e.target.closest('[data-tocar]')) abrir(); };
    c.onkeydown = (e) => { if (e.key === 'Enter') abrir(); };
  });
  $$('[data-tocar]', el).forEach((b) => (b.onclick = () => tocar(musicas.get(b.dataset.tocar), 'audio', lista)));
}

function verBuscar() {
  vista.innerHTML = `
    <div class="busca-topo">
      <form class="campo-busca" id="formBusca" autocomplete="off">
        ${ICONE.buscar}
        <input id="q" type="search" placeholder="Cole o link da música ou digite o nome..." aria-label="Buscar música" value="${esc(ultimaBusca.q)}">
        <button class="botao principal" type="submit">Buscar</button>
      </form>
      <div class="dica-busca">
        <span>Funciona com links do <span class="selo-fonte">Spotify</span><span class="selo-fonte">YouTube</span><span class="selo-fonte">YouTube Music</span><span class="selo-fonte">Deezer</span><span class="selo-fonte">Apple Music</span><span class="selo-fonte">SoundCloud</span></span>
      </div>
    </div>
    <div class="pagina" style="padding-top:8px" id="resBusca"></div>`;
  const q = $('#q');
  q.focus();
  let t;
  $('#formBusca').onsubmit = (e) => { e.preventDefault(); buscar(q.value); };
  q.addEventListener('paste', () => setTimeout(() => /https?:\/\//.test(q.value) && buscar(q.value), 30));
  q.addEventListener('input', () => {
    clearTimeout(t);
    if (!/https?:\/\//.test(q.value) && q.value.trim().length >= 3) t = setTimeout(() => buscar(q.value), 550);
  });
  if (ultimaBusca.r) mostrarResultados(ultimaBusca.r);
  else mostrarHistoricoNaBusca();
}

async function mostrarHistoricoNaBusca() {
  const el = $('#resBusca');
  const h = await api('historico').catch(() => []);
  if (!$('#resBusca') || ultimaBusca.r) return;
  el.innerHTML = h.length
    ? `<h2 style="margin-top:6px">Abertas recentemente</h2><div class="resultados">${h.slice(0, 12).map((f) => cartao(f)).join('')}</div>`
    : `<div class="vazio">Digite o nome de uma música — ou cole um link — para ver a letra traduzida, o clipe e criar posts.</div>`;
  ligarCartoes(el, h);
}

let buscaN = 0;
async function buscar(q) {
  q = q.trim();
  if (!q) return;
  const n = ++buscaN;
  const el = $('#resBusca');
  el.innerHTML = `<div class="vazio"><span class="carregando"></span><br><br>${/https?:\/\//.test(q) ? 'Lendo o link...' : 'Buscando...'}</div>`;
  try {
    const r = await api('buscar?q=' + enc(q));
    if (n !== buscaN) return;
    ultimaBusca = { q, r };
    mostrarResultados(r);
    if (r.principal && /https?:\/\//.test(q)) location.hash = '#/musica/' + enc(r.principal.id); // link: já abre a música
  } catch (e) {
    el.innerHTML = `<div class="vazio">Não consegui buscar agora (${esc(e.message)}).</div>`;
  }
}

function mostrarResultados(r) {
  const el = $('#resBusca');
  if (!el) return;
  const outros = r.principal ? r.resultados.slice(1) : r.resultados;
  el.innerHTML = (r.aviso ? `<div class="aviso-letra">${esc(r.aviso)}</div>` : '') +
    (r.principal ? `<div class="resultados">${cartao(r.principal, true)}</div>${outros.length ? '<h2>Relacionadas</h2>' : ''}` : '') +
    (outros.length ? `<div class="resultados">${outros.map((f) => cartao(f)).join('')}</div>` : (r.principal ? '' : '<div class="vazio">Nada encontrado 😕</div>'));
  ligarCartoes(el, r.resultados);
}

// ---------------- Página da música ----------------
let MUS = null; // {f, letra, sel:Set, ancora}
async function verMusica(id) {
  let f = musicas.get(id);
  if (!f) {
    const h = await api('historico').catch(() => []);
    h.forEach((x) => musicas.set(x.id, x));
    f = musicas.get(id);
  }
  if (!f) { location.hash = '#/buscar'; return; }
  MUS = { f, letra: null, sel: new Set(), ancora: null, idx: -2, idioma: CONFIG.idioma || 'pt' };
  corDaCapa(f.capa || f.capa_hd);
  vista.innerHTML = `
    <section class="heroi">
      <div class="heroi-conteudo">
        <img class="capa-grande" src="${esc(f.capa_hd || f.capa || 'icone.png')}" alt="" onerror="this.src='icone.png'">
        <div style="min-width:0">
          <div class="rotulo">Música ${f.fonte ? '· ' + esc(f.fonte) : ''}</div>
          <h1>${esc(f.titulo)}</h1>
          <div class="meta"><b>${esc(f.artista)}</b>${f.album ? ` · ${esc(f.album)}` : ''}${f.duracao ? ` · ${mmss(f.duracao)}` : ''}</div>
        </div>
      </div>
    </section>
    <div class="acoes">
      <button class="botao principal" id="mOuvir">${ICONE.play} Ouvir aqui</button>
      <button class="botao" id="mClipe">${ICONE.video} Ver clipe</button>
      <button class="botao fantasma" id="mPost">${ICONE.post} Criar post</button>
      <button class="botao fantasma" id="mCopiar">${ICONE.copiar} Copiar letra</button>
      <select class="seletor idioma" id="mIdioma" aria-label="Idioma da tradução">
        ${Object.entries(IDIOMAS).map(([k, v]) => `<option value="${k}" ${k === MUS.idioma ? 'selected' : ''}>${v}</option>`).join('')}
      </select>
    </div>
    <div class="musica-corpo">
      <div>
        <div class="selecao-barra" id="mSel"></div>
        <div id="mLetra"><div class="vazio"><span class="carregando"></span><br><br>Buscando a letra e traduzindo...</div></div>
      </div>
      <aside class="lado-video">
        <div class="vaga-video" id="mVaga"></div>
        <div class="info-video" id="mInfoVideo"></div>
      </aside>
    </div>`;
  $('#mOuvir').onclick = () => tocar(f, 'audio', ultimaBusca.r?.resultados?.some((x) => x.id === f.id) ? ultimaBusca.r.resultados : [f]);
  $('#mClipe').onclick = () => tocar(f, 'clipe');
  $('#mPost').onclick = () => postDaMusica();
  $('#mCopiar').onclick = copiarLetra;
  $('#mIdioma').onchange = (e) => { MUS.idioma = e.target.value; carregarLetraMusica(); };
  desenharInfoVideo();
  desenharSelecao();
  carregarLetraMusica();
}

function desenharInfoVideo() {
  const el = $('#mInfoVideo');
  if (!el || !MUS) return;
  const tocandoEsta = P.faixa && P.faixa.id === MUS.f.id;
  const vaga = $('#mVaga');
  if (tocandoEsta) vaga.dataset.sempre = '1'; else delete vaga.dataset.sempre;
  if (!tocandoEsta) {
    vaga.innerHTML = `<button class="botao" style="margin:auto;display:flex;height:100%;width:100%;justify-content:center;border-radius:14px;background:#111" id="mVagaPlay">${ICONE.video} Ver o clipe aqui</button>`;
    $('#mVagaPlay').onclick = () => tocar(MUS.f, 'clipe');
    el.innerHTML = 'O vídeo toca aqui dentro, com a letra sincronizada ao lado.';
  } else {
    vaga.innerHTML = '';
    el.innerHTML = `${P.modo === 'clipe' ? 'Clipe' : 'Versão de áudio'}: ${esc(P.video?.titulo || '')}
      · <button id="mTrocar">trocar vídeo</button> · <button id="mModo">${P.modo === 'clipe' ? 'ouvir versão de estúdio' : 'ver o clipe'}</button>`;
    $('#mTrocar').onclick = trocarVideo;
    $('#mModo').onclick = () => tocar(MUS.f, P.modo === 'clipe' ? 'audio' : 'clipe');
  }
  posicionarPalco();
}

async function carregarLetraMusica() {
  const m = MUS;
  $('#mLetra').innerHTML = '<div class="vazio"><span class="carregando"></span><br><br>Buscando a letra e traduzindo...</div>';
  try {
    m.letra = await letraDaFaixa(m.f, m.idioma);
  } catch (e) {
    if (MUS === m) $('#mLetra').innerHTML = `<div class="vazio">Não consegui buscar a letra (${esc(e.message)})</div>`;
    return;
  }
  if (MUS !== m) return;
  desenharLetra();
}

function desenharLetra() {
  const l = MUS.letra;
  const el = $('#mLetra');
  if (!l.linhas.length) {
    el.innerHTML = `<div class="vazio">${l.instrumental ? 'Música instrumental ♪' : 'Não achei a letra dessa música 😕'}</div>`;
    return;
  }
  el.innerHTML = (l.sincronizada ? '' : '<div class="aviso-letra">Essa letra não tem o tempo de cada linha — dá para criar imagem, mas o vídeo com música precisa da letra sincronizada.</div>') +
    (l.erro_trad ? `<div class="aviso-letra">${esc(l.erro_trad)}</div>` : '') +
    `<div class="letra">${l.linhas.map(([t, orig], i) => {
      const tr = l.trad[i] || '';
      if (!orig) return `<div class="verso instrumental" data-i="${i}"><span class="t">${seg(t)}</span><span class="tr">♪</span></div>`;
      const mostra = tr && normalizar(tr) !== normalizar(orig);
      return `<div class="verso" data-i="${i}" title="Clique para marcar (Shift = intervalo)"><span class="t">${seg(t)}</span><span class="tr">${esc(tr || orig)}</span>${mostra ? `<span class="og">${esc(orig)}</span>` : ''}</div>`;
    }).join('')}</div>
    <p class="sub" style="margin-top:20px;font-size:12px">Letra: ${esc(l.fonte || '—')} · tradução automática</p>`;
  $$('.verso', el).forEach((v) => (v.onclick = (e) => marcar(Number(v.dataset.i), e.shiftKey)));
  $$('.verso .t', el).forEach((t) => (t.ondblclick = (e) => {
    e.stopPropagation();
    const i = Number(t.parentElement.dataset.i);
    if (P.faixa?.id === MUS.f.id && l.linhas[i][0] != null) comandoLocal('seek', Math.max(0, l.linhas[i][0] - (E.interno ? E.offset || 0 : 0)));
  }));
  pintarSelecao();
}

function marcar(i, shift) {
  const l = MUS.letra;
  if (!l.linhas[i][1]) return;
  if (shift && MUS.ancora != null) {
    const [a, b] = [MUS.ancora, i].sort((x, y) => x - y);
    for (let k = a; k <= b; k++) if (l.linhas[k][1]) MUS.sel.add(k);
  } else {
    MUS.sel.has(i) ? MUS.sel.delete(i) : MUS.sel.add(i);
    MUS.ancora = i;
  }
  pintarSelecao();
}

function pintarSelecao() {
  $$('#mLetra .verso').forEach((v) => v.classList.toggle('marcado', MUS.sel.has(Number(v.dataset.i))));
  desenharSelecao();
}

function desenharSelecao() {
  const n = MUS.sel.size;
  $('#mSel').innerHTML = n
    ? `<span><b style="color:var(--texto)">${n}</b> linha${n > 1 ? 's' : ''} marcada${n > 1 ? 's' : ''}</span>
       <button class="botao principal" id="mSelPost">${ICONE.post} Criar post</button>
       <button class="botao fantasma" id="mSelLimpar">Limpar</button>`
    : '<span>Clique nas linhas para marcar o trecho do post (Shift = intervalo). Duplo clique no tempo pula pra lá.</span>';
  if (n) {
    $('#mSelPost').onclick = postDaMusica;
    $('#mSelLimpar').onclick = () => { MUS.sel.clear(); pintarSelecao(); };
  }
}

function marcarMusica() {
  if (!MUS?.letra?.sincronizada || P.faixa?.id !== MUS.f.id) return;
  const t = ytTempo() + (E.interno ? E.offset || 0 : 0);
  const ls = MUS.letra.linhas;
  let idx = -1;
  for (let i = 0; i < ls.length; i++) { if (ls[i][0] <= t) idx = i; else break; }
  if (idx === MUS.idx) return;
  MUS.idx = idx;
  $$('#mLetra .verso').forEach((v, i) => { v.classList.toggle('atual', i === idx); v.classList.toggle('passado', i < idx); });
}

function copiarLetra() {
  const l = MUS?.letra;
  if (!l?.linhas.length) return;
  const txt = l.linhas.map(([, o], i) => (o ? `${l.trad[i] || o}${l.trad[i] && normalizar(l.trad[i]) !== normalizar(o) ? `\n(${o})` : ''}` : '')).join('\n');
  navigator.clipboard.writeText(`${MUS.f.titulo} — ${MUS.f.artista}\n\n${txt}`).then(() => aviso('Letra copiada!'));
}

// ---------------- Estúdio de post ----------------
function novoPost(meta, linhas, sincronizada, sel) {
  const sels = [...sel].filter((i) => linhas[i] && linhas[i][1]).sort((a, b) => a - b);
  POST = {
    meta, linhas, sincronizada, sel: new Set(sels), ancora: null,
    formato: localStorage.getItem('eco_formato') || 'Stories', estilo: localStorage.getItem('eco_estilo') || 'vidro',
    destaque_original: false, assinatura: true,
    audio: null, audio_tipo: '', baixando: false, ini: 0, fim: 0, editouTrecho: false, fade_in: 1, fade_out: 1.5,
  };
  trechoPadrao();
  location.hash = '#/post';
}

function postDaMusica() {
  const l = MUS?.letra;
  if (!l?.linhas.length) return aviso('Essa música ainda não tem letra carregada', true);
  const linhas = l.linhas.map(([t, o], i) => [t, o, l.trad[i] || '']);
  let sel = MUS.sel;
  if (!sel.size) sel = new Set(linhas.map((_, i) => i).filter((i) => linhas[i][1]).slice(0, 3));
  novoPost({ ...MUS.f }, linhas, l.sincronizada, sel);
}

function postDoQueToca() {
  if (!LETRA.linhas.length) return aviso('A música que está tocando ainda não tem letra', true);
  const linhas = LETRA.linhas.map(([t, o], i) => [t, o, LETRA.trad[i] || '']);
  const t = posAgora() + (E.offset || 0);
  let idx = 0;
  linhas.forEach((l, i) => { if (l[0] <= t) idx = i; });
  const sel = new Set([idx, idx + 1].filter((i) => linhas[i] && linhas[i][1]));
  const meta = { id: 'agora', titulo: E.titulo, artista: E.artista, duracao: E.duracao };
  if (E.interno && P.faixa) Object.assign(meta, { capa: P.faixa.capa, capa_hd: P.faixa.capa_hd, preview: P.faixa.preview, id: P.faixa.id, video_audio: P.modo === 'audio' ? P.video?.id : null });
  novoPost(meta, linhas, true, sel.size ? sel : new Set([idx]));
}

function trechoPadrao() {
  const s = [...POST.sel].sort((a, b) => a - b);
  if (!s.length || !POST.sincronizada) return;
  const L = POST.linhas;
  const ini = Math.max(0, (L[s[0]][0] || 0) - 0.6);
  let fim = null;
  for (let i = s[s.length - 1] + 1; i < L.length; i++) if (L[i][0] != null) { fim = L[i][0] + 0.3; break; }
  if (fim == null) fim = (L[s[s.length - 1]][0] || 0) + 5;
  POST.ini = Math.round(ini * 100) / 100;
  POST.fim = Math.round(Math.min(fim, ini + 90) * 100) / 100;
}

function dadosPost(extra = {}) {
  const s = [...POST.sel].sort((a, b) => a - b);
  return {
    ...POST.meta, linhas: s.map((i) => POST.linhas[i]), formato: POST.formato, estilo: POST.estilo,
    destaque_original: POST.destaque_original, assinatura: POST.assinatura, ...extra,
  };
}

function verPost() {
  if (!POST) {
    vista.innerHTML = `<div class="pagina"><h1>Criar post</h1><p class="sub">Imagem ou vídeo com a letra traduzida — e a música tocando de fundo.</p>
      <div class="linha-botoes"><button class="botao principal" id="pAgora">${ICONE.fone} Usar a música que está tocando</button>
      <a class="botao" href="#/buscar">${ICONE.buscar} Buscar uma música pelo nome ou link</a></div></div>`;
    $('#pAgora').onclick = postDoQueToca;
    return;
  }
  const m = POST.meta;
  vista.innerHTML = `
    <div class="pagina">
      <h1>Criar post</h1>
      <p class="sub">${esc(m.titulo)} — ${esc(m.artista)}</p>
      <div class="estudio">
        <section class="painel"><h3>Linhas do post</h3><div class="lista-linhas" id="psLinhas"></div></section>
        <section class="previa">
          <img class="previa-img" id="psImg" alt="Prévia do post">
          <div class="linha-botoes" style="justify-content:center">
            <button class="botao principal" id="psSalvar">${ICONE.baixar} Salvar PNG</button>
            <button class="botao" id="psCopiar">${ICONE.copiar} Copiar imagem</button>
          </div>
          <div class="status" id="psStatusImg"></div>
        </section>
        <section class="opcoes">
          <div class="painel"><h3>Estilo</h3><div class="estilos" id="psEstilos">
            ${Object.entries(ESTILOS).map(([k, v]) => `<button class="estilo ${k === POST.estilo ? 'ativo' : ''}" data-estilo="${k}" title="${v}"><img alt=""><span>${v}</span></button>`).join('')}
          </div></div>
          <div class="painel"><h3>Formato</h3><div class="chips" id="psFormatos">
            ${['Stories', 'Feed', 'Quadrado'].map((k) => `<button class="chip ${k === POST.formato ? 'ativo' : ''}" data-formato="${k}">${k}</button>`).join('')}
          </div>
          <div style="height:14px"></div>
          <h3>Destaque</h3><div class="segmentado" id="psDestaque"><button data-v="0">Tradução</button><button data-v="1">Original</button></div>
          <div style="height:14px"></div>
          <label class="alternar">Assinatura “Ecolyra · by davicjc”<input type="checkbox" id="psAssin" ${POST.assinatura ? 'checked' : ''}></label>
          </div>
          <div class="painel" id="psVideo"></div>
        </section>
      </div>
    </div>`;
  desenharLinhasPost();
  $$('#psEstilos [data-estilo]').forEach((b) => (b.onclick = () => { POST.estilo = b.dataset.estilo; localStorage.setItem('eco_estilo', POST.estilo); marcarOpcoes(); previa(); }));
  $$('#psFormatos [data-formato]').forEach((b) => (b.onclick = () => { POST.formato = b.dataset.formato; localStorage.setItem('eco_formato', POST.formato); marcarOpcoes(); previa(true); }));
  $$('#psDestaque button').forEach((b) => (b.onclick = () => { POST.destaque_original = b.dataset.v === '1'; marcarOpcoes(); previa(true); }));
  $('#psAssin').onchange = (e) => { POST.assinatura = e.target.checked; previa(true); };
  $('#psSalvar').onclick = async () => {
    const r = await api('post/imagem', dadosPost({ acao: 'salvar' })).catch((e) => ({ erro: e.message }));
    if (r.arquivo) { statusImg(`Salvo em Imagens\\Ecolyra`, 'ok', r.arquivo); } else statusImg(r.erro || 'Falhou', 'erro');
  };
  $('#psCopiar').onclick = async () => {
    const r = await api('post/imagem', dadosPost({ acao: 'copiar' })).catch(() => ({}));
    statusImg(r.ok ? 'Copiada! É só colar (Ctrl+V) no WhatsApp, Instagram...' : 'Não consegui copiar', r.ok ? 'ok' : 'erro');
  };
  marcarOpcoes();
  desenharPainelVideo();
  previa(true);
}

function statusImg(txt, cls = '', arquivo = null) {
  const s = $('#psStatusImg');
  s.className = 'status ' + cls;
  s.innerHTML = esc(txt) + (arquivo ? ` · <a href="#" id="psAbrirImg">mostrar na pasta</a>` : '');
  if (arquivo) $('#psAbrirImg').onclick = (e) => { e.preventDefault(); api('abrir', { caminho: arquivo }); };
}

function marcarOpcoes() {
  $$('#psEstilos [data-estilo]').forEach((b) => b.classList.toggle('ativo', b.dataset.estilo === POST.estilo));
  $$('#psFormatos [data-formato]').forEach((b) => b.classList.toggle('ativo', b.dataset.formato === POST.formato));
  $$('#psDestaque button').forEach((b) => b.classList.toggle('ativo', (b.dataset.v === '1') === POST.destaque_original));
  const img = $('#psImg');
  if (img) img.style.aspectRatio = { Stories: '9 / 16', Feed: '4 / 5', Quadrado: '1 / 1' }[POST.formato];
}

function desenharLinhasPost() {
  const el = $('#psLinhas');
  el.innerHTML = POST.linhas.map(([t, o, tr], i) => (o
    ? `<div class="verso ${POST.sel.has(i) ? 'marcado' : ''}" data-i="${i}"><span class="t">${seg(t)}</span><span class="tr">${esc(tr || o)}</span>${tr && normalizar(tr) !== normalizar(o) ? `<span class="og">${esc(o)}</span>` : ''}</div>`
    : '')).join('');
  $$('.verso', el).forEach((v) => (v.onclick = (e) => {
    const i = Number(v.dataset.i);
    if (e.shiftKey && POST.ancora != null) {
      const [a, b] = [POST.ancora, i].sort((x, y) => x - y);
      for (let k = a; k <= b; k++) if (POST.linhas[k][1]) POST.sel.add(k);
    } else { POST.sel.has(i) ? POST.sel.delete(i) : POST.sel.add(i); POST.ancora = i; }
    $$('.verso', el).forEach((x) => x.classList.toggle('marcado', POST.sel.has(Number(x.dataset.i))));
    if (!POST.editouTrecho) trechoPadrao();
    previa(true);
    desenharPainelVideo();
  }));
  const primeiro = $('.verso.marcado', el);
  if (primeiro) primeiro.scrollIntoView({ block: 'center' });
}

let previaN = 0, previaT, miniaturasT;
function previa(miniaturas = false) {
  clearTimeout(previaT);
  previaT = setTimeout(async () => {
    const img = $('#psImg');
    if (!img || !POST) return;
    if (!POST.sel.size) { img.removeAttribute('src'); statusImg('Marque pelo menos uma linha'); return; }
    const n = ++previaN;
    img.classList.add('carregando-img');
    try {
      const r = await api('post/imagem', dadosPost());
      if (n === previaN && $('#psImg')) { img.src = r.img; img.classList.remove('carregando-img'); statusImg(''); }
    } catch (e) { statusImg('Erro na prévia: ' + e.message, 'erro'); }
    if (miniaturas) { clearTimeout(miniaturasT); miniaturasT = setTimeout(desenharMiniaturas, 300); }
  }, 180);
}

async function desenharMiniaturas() {
  const alvo = JSON.stringify(dadosPost());
  for (const k of Object.keys(ESTILOS)) {
    if (!POST || JSON.stringify(dadosPost()) !== alvo || !$('#psEstilos')) return;
    try {
      const r = await api('post/imagem', dadosPost({ estilo: k }));
      const im = $(`#psEstilos [data-estilo="${k}"] img`);
      if (im) im.src = r.img;
    } catch (e) { /* fica sem miniatura */ }
  }
}

function desenharPainelVideo() {
  const el = $('#psVideo');
  if (!el) return;
  if (!POST.sincronizada) {
    el.innerHTML = '<h3>Vídeo com música</h3><p class="status">Essa letra não tem o tempo de cada linha, então o vídeo com música não está disponível. A imagem funciona normal.</p>';
    return;
  }
  if (!POST.audio && !POST.baixando && POST.sel.size) prepararAudio();
  const dur = Math.max(0, POST.fim - POST.ini);
  el.innerHTML = `
    <h3>Vídeo com música (MP4)</h3>
    <div class="status ${POST.audio ? 'ok' : ''}" id="psAudioSt">${POST.audio ? (POST.audio_tipo === 'previa' ? 'Música pronta (prévia de 30 s — o trecho pode não bater)' : 'Música pronta ✓') : POST.baixando ? '<span class="carregando" style="width:12px;height:12px;vertical-align:-1px"></span> Preparando a música de fundo...' : 'Marque as linhas para preparar a música'}</div>
    <div style="height:10px"></div>
    <div class="trecho">
      <label>Começa em<div class="ajuste"><button data-n="ini" data-d="-0.25">−</button><input id="psIni" value="${num(POST.ini)}"><button data-n="ini" data-d="0.25">+</button></div></label>
      <label>Termina em<div class="ajuste"><button data-n="fim" data-d="-0.25">−</button><input id="psFim" value="${num(POST.fim)}"><button data-n="fim" data-d="0.25">+</button></div></label>
    </div>
    <p class="status" style="margin:6px 0 12px">Trecho de <b>${num(dur)} s</b> (${mmss(POST.ini)} → ${mmss(POST.fim)})</p>
    <div class="deslizante"><div>Fade-in <b id="psFiV">${num(POST.fade_in)} s</b></div><input type="range" min="0" max="5" step="0.25" value="${POST.fade_in}" id="psFi"></div>
    <div style="height:8px"></div>
    <div class="deslizante"><div>Fade-out <b id="psFoV">${num(POST.fade_out)} s</b></div><input type="range" min="0" max="6" step="0.25" value="${POST.fade_out}" id="psFo"></div>
    <div style="height:14px"></div>
    <div class="linha-botoes">
      <button class="botao" id="psOuvir" ${POST.audio ? '' : 'disabled'}>${ICONE.fone} Ouvir trecho</button>
      <button class="botao principal" id="psExportar" ${POST.audio && POST.sel.size ? '' : 'disabled'}>${ICONE.video} Exportar vídeo</button>
    </div>
    <audio id="psAudio" controls hidden></audio>
    <div style="height:12px"></div>
    <div class="progresso" id="psProg" hidden><div></div></div>
    <div class="status" id="psVidSt"></div>`;
  $$('.ajuste button', el).forEach((b) => (b.onclick = () => {
    POST[b.dataset.n] = Math.max(0, Math.round((POST[b.dataset.n] + Number(b.dataset.d)) * 100) / 100);
    POST.editouTrecho = true;
    desenharPainelVideo();
  }));
  for (const [id, k] of [['psIni', 'ini'], ['psFim', 'fim']]) {
    $('#' + id).onchange = (e) => {
      const v = parseFloat(e.target.value.replace(',', '.'));
      if (!isNaN(v)) { POST[k] = Math.max(0, v); POST.editouTrecho = true; }
      desenharPainelVideo();
    };
  }
  $('#psFi').oninput = (e) => { POST.fade_in = Number(e.target.value); $('#psFiV').textContent = num(POST.fade_in) + ' s'; };
  $('#psFo').oninput = (e) => { POST.fade_out = Number(e.target.value); $('#psFoV').textContent = num(POST.fade_out) + ' s'; };
  $('#psOuvir').onclick = ouvirTrecho;
  $('#psExportar').onclick = exportarVideo;
}

async function esperarTarefa(id, aoProgresso) {
  for (;;) {
    const t = await api('tarefa?id=' + id);
    if (t.erro) throw new Error(t.erro);
    aoProgresso && aoProgresso(t);
    if (t.resultado) return t.resultado;
    await new Promise((r) => setTimeout(r, 450));
  }
}

async function prepararAudio() {
  const post = POST;
  post.baixando = true;
  try {
    const { tarefa } = await api('post/audio', { titulo: post.meta.titulo, artista: post.meta.artista, duracao: post.meta.duracao || 0, preview: post.meta.preview || '', video_audio: post.meta.video_audio || null });
    const r = await esperarTarefa(tarefa);
    post.audio = r.audio; post.audio_tipo = r.tipo;
  } catch (e) {
    aviso('Não consegui baixar a música: ' + e.message, true, 5000);
  }
  post.baixando = false;
  if (POST === post && rotaAtual === 'post') desenharPainelVideo();
}

async function ouvirTrecho() {
  const a = $('#psAudio');
  $('#psOuvir').disabled = true;
  try {
    const r = await api('post/previa', { audio: POST.audio, ini: POST.ini, fim: POST.fim, fade_in: POST.fade_in, fade_out: POST.fade_out });
    a.hidden = false;
    a.src = `/api/arquivo?id=${r.id}&t=${enc(CFG.token)}`;
    if (ytTocando()) P.yt.pauseVideo();
    a.play();
  } catch (e) { aviso(e.message, true); }
  $('#psOuvir').disabled = false;
}

async function exportarVideo() {
  if (POST.fim - POST.ini < 1) return aviso('O trecho precisa ter pelo menos 1 segundo', true);
  const prog = $('#psProg'), st = $('#psVidSt'), bt = $('#psExportar');
  prog.hidden = false; bt.disabled = true; st.className = 'status';
  const todas = POST.linhas.map(([t, o, tr]) => [t ?? 0, o, tr]);
  try {
    const { tarefa } = await api('post/video', { ...dadosPost(), todas, sel: [...POST.sel].sort((a, b) => a - b), audio: POST.audio, ini: POST.ini, fim: POST.fim, fade_in: POST.fade_in, fade_out: POST.fade_out });
    const r = await esperarTarefa(tarefa, (t) => { prog.firstElementChild.style.width = Math.round(t.progresso * 100) + '%'; st.textContent = t.texto; });
    prog.firstElementChild.style.width = '100%';
    st.className = 'status ok';
    st.innerHTML = `Vídeo salvo em Vídeos\\Ecolyra · <a href="#" id="psAbrirVid">mostrar na pasta</a>`;
    $('#psAbrirVid').onclick = (e) => { e.preventDefault(); api('abrir', { caminho: r.arquivo }); };
    aviso('Vídeo pronto! 🎬');
  } catch (e) {
    st.className = 'status erro'; st.textContent = 'Falhou: ' + e.message;
  }
  bt.disabled = false;
}

// ---------------- Histórico ----------------
async function verHistorico() {
  vista.innerHTML = '<div class="pagina"><h1>Histórico</h1><p class="sub">Músicas que você abriu no Ecolyra.</p><div id="hLista"><div class="vazio"><span class="carregando"></span></div></div></div>';
  const h = await api('historico').catch(() => []);
  const el = $('#hLista');
  if (!el) return;
  el.innerHTML = h.length ? `<div class="resultados">${h.map((f) => cartao(f)).join('')}</div>` : '<div class="vazio">Nada por aqui ainda. <a href="#/buscar">Buscar uma música</a></div>';
  ligarCartoes(el, h);
}

// ---------------- Configurações ----------------
let CONFIG = {};
async function carregarConfig() { CONFIG = await api('config').catch(() => CONFIG); return CONFIG; }

async function verConfig() {
  await carregarConfig();
  const c = CONFIG;
  const seg = (k, ops) => `<div class="segmentado" data-k="${k}">${ops.map(([v, t]) => `<button data-v="${v}" class="${String(c[k]) === String(v) ? 'ativo' : ''}">${t}</button>`).join('')}</div>`;
  vista.innerHTML = `
    <div class="pagina"><h1>Configurações</h1><p class="sub">Valem para a plataforma e para o overlay.</p>
    <div class="config">
      <h2 style="margin-top:0">Modo de exibição</h2>
      <div class="modos">
        <button class="modo ${c.modo !== 'plataforma' ? 'ativo' : ''}" data-modo="flutuante">
          <div class="desenho"><i style="left:8%;top:12%;width:60%;height:70%"></i><i class="v" style="left:46%;top:52%;width:48%;height:30%"></i></div>
          <b>Telas flutuantes</b><span>O overlay da letra fica por cima de tudo (jogos, navegador...). A busca abre numa janelinha.</span>
        </button>
        <button class="modo ${c.modo === 'plataforma' ? 'ativo' : ''}" data-modo="plataforma">
          <div class="desenho"><i style="left:6%;top:10%;width:14%;height:80%"></i><i class="v" style="left:24%;top:10%;width:70%;height:62%"></i><i style="left:24%;top:78%;width:70%;height:12%"></i></div>
          <b>Aba única (plataforma)</b><span>Tudo numa janela só, como um app de música: busca, letra, vídeo e posts.</span>
        </button>
      </div>
      <h2>Letra</h2>
      <div class="opcao"><div><b>Idioma da tradução</b><p>Para onde a letra é traduzida.</p></div>
        <select class="seletor" id="cIdioma">${Object.entries(IDIOMAS).map(([k, v]) => `<option value="${k}" ${k === c.idioma ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
      <div class="opcao"><div><b>Sincronia pelo áudio</b><p>Escuta o som (Shazam) para reconhecer a música e acertar o tempo da letra em vídeos.</p></div>
        ${seg('sinc_audio', [['videos', 'Vídeos'], ['sempre', 'Sempre'], ['off', 'Desligada']])}</div>
      <div class="opcao"><div><b>Overlay: frase original</b><p>Mostra a linha na língua original acima da tradução.</p></div>
        <label class="alternar"><input type="checkbox" data-bool="original" ${c.original ? 'checked' : ''}></label></div>
      <div class="opcao"><div><b>Overlay: próxima linha</b><p>Mostra a próxima linha em cinza embaixo.</p></div>
        <label class="alternar"><input type="checkbox" data-bool="proxima" ${c.proxima ? 'checked' : ''}></label></div>
      <h2>Sistema</h2>
      <div class="opcao"><div><b>Iniciar com o Windows</b><p>Abre o Ecolyra junto com o computador.</p></div>
        <label class="alternar"><input type="checkbox" data-bool="iniciar_windows" ${c.iniciar_windows ? 'checked' : ''}></label></div>
      <div class="opcao"><div><b>Atualizar automaticamente</b><p>Quando sair versão nova no GitHub, o Ecolyra baixa, instala e reinicia sozinho.</p></div>
        <label class="alternar"><input type="checkbox" data-bool="atualizar_auto" ${c.atualizar_auto ? 'checked' : ''}></label></div>
      <div class="opcao"><div><b>Versão ${esc(c.versao || CFG.versao)}</b><p id="cUpd">${esc(c.atualizacao?.msg || 'Verificação automática a cada 6 horas.')}</p></div>
        <div class="linha-botoes"><button class="botao" id="cVerificar">Verificar agora</button></div></div>
    </div></div>`;
  $$('[data-modo]').forEach((b) => (b.onclick = async () => {
    await api('config', { modo: b.dataset.modo });
    aviso(b.dataset.modo === 'plataforma' ? 'Modo aba única: o overlay fica escondido (volta pela bandeja)' : 'Modo telas flutuantes: o overlay voltou');
    setTimeout(verConfig, 300);
  }));
  $('#cIdioma').onchange = (e) => api('config', { idioma: e.target.value }).then(() => { CONFIG.idioma = e.target.value; aviso('Idioma trocado'); });
  $$('.segmentado[data-k]').forEach((s) => $$('button', s).forEach((b) => (b.onclick = () => api('config', { [s.dataset.k]: b.dataset.v }).then(() => setTimeout(verConfig, 250)))));
  $$('[data-bool]').forEach((i) => (i.onchange = () => api('config', { [i.dataset.bool]: i.checked })));
  $('#cVerificar').onclick = async () => {
    $('#cVerificar').disabled = true;
    let st = await api('atualizar', { acao: 'verificar' });
    while (['verificando', 'baixando'].includes(st.estado)) {
      $('#cUpd').textContent = st.msg;
      await new Promise((r) => setTimeout(r, 700));
      st = (await api('config')).atualizacao;
    }
    if ($('#cUpd')) { $('#cUpd').textContent = st.msg; $('#cVerificar').disabled = false; }
  };
}

// ---------------- Sobre ----------------
function verSobre() {
  vista.innerHTML = `
    <div class="pagina"><div class="sobre">
      <div class="sobre-topo"><img src="icone.png" alt=""><div><h1>Ecolyra</h1><p class="sub" style="margin:0">Versão ${esc(CFG.versao)} · letras traduzidas em tempo real, que escutam a música</p></div></div>
      <div class="painel criador">
        <div class="assin">// criador<b>${esc(CFG.criador)}</b>Este software foi criado por ${esc(CFG.criador.toLowerCase())}.</div>
        <div class="linha-botoes" style="margin-left:auto">
          <button class="botao principal" id="sPortfolio">${ICONE.ext} Visitar portfólio</button>
          <button class="botao" id="sGithub">${ICONE.ext} GitHub</button>
        </div>
      </div>
      <h2>De onde vêm os dados</h2>
      <div class="apis">
        <div><b>Windows Media Controls</b>o que toca no Spotify, YouTube, Deezer...</div>
        <div><b>Deezer + iTunes</b>busca por nome, links, capas em HD</div>
        <div><b>LRCLIB + NetEase</b>letras sincronizadas</div>
        <div><b>Google Tradutor / MyMemory</b>tradução das letras</div>
        <div><b>Shazam + Whisper</b>reconhece a música pelo áudio</div>
        <div><b>YouTube</b>vídeos e música dentro do app</div>
        <div><b>yt-dlp + FFmpeg</b>vídeo do post com a música de fundo</div>
        <div><b>GitHub Releases</b>atualização automática</div>
      </div>
    </div></div>`;
  $('#sPortfolio').onclick = () => api('link', { url: CFG.portfolio });
  $('#sGithub').onclick = () => api('link', { url: `https://github.com/${CFG.repo}` });
}

// ---------------- atalhos ----------------
addEventListener('keydown', (e) => {
  const digitando = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.key === '/' && !digitando)) {
    e.preventDefault();
    location.hash = '#/buscar';
    setTimeout(() => $('#q')?.focus(), 50);
  } else if (e.key === ' ' && !digitando) { e.preventDefault(); comando('pp'); }
  else if (e.key === 'Escape' && P.grande) { P.grande = false; posicionarPalco(); }
});

// ---------------- início ----------------
carregarConfig().then(() => { navegar(); sincronizar(); requestAnimationFrame(quadro); });
