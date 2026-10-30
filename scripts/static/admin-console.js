const $ = (id) => document.getElementById(id);
function setTheme(theme){document.documentElement.dataset.theme=theme;document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#0f1b2b':'#edf2f7');try{localStorage.setItem('cortex-theme',theme)}catch(_){}try{$('training-frame')?.contentDocument?.documentElement?.setAttribute('data-theme',theme);}catch(_){}document.querySelectorAll('[data-theme-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.themeChoice===theme)));}
document.querySelectorAll('[data-theme-choice]').forEach(button=>button.addEventListener('click',()=>setTheme(button.dataset.themeChoice)));
setTheme(document.documentElement.dataset.theme==='light'?'light':'dark');
let trainingFrameObserver;
function fitTrainingFrame(){
  const frame=$('training-frame');
  try{
    const body=frame?.contentDocument?.body;
    if(!body)return;
    const top=Math.max(0,frame.getBoundingClientRect().top);
    const available=Math.max(320,window.innerHeight-top-(window.innerWidth<=760?80:20));
    frame.contentDocument.documentElement.style.setProperty('--admin-viewport-height',available+'px');
    const dialogOpen=!!frame.contentDocument.querySelector('dialog[open]');
    const height=dialogOpen?available:Math.max(620,Math.ceil(body.getBoundingClientRect().height)+4);
    if(Math.abs(frame.offsetHeight-height)>2)frame.style.height=height+'px';
  }catch(_){}
}
function watchTrainingFrame(){
  const frame=$('training-frame');
  try{
    const body=frame?.contentDocument?.body;
    if(!body)return;
    trainingFrameObserver?.disconnect();
    trainingFrameObserver=new ResizeObserver(()=>requestAnimationFrame(fitTrainingFrame));
    trainingFrameObserver.observe(body);
    fitTrainingFrame();
  }catch(_){}
}
$('training-frame')?.addEventListener('load',watchTrainingFrame);
if($('training-frame')?.contentDocument?.readyState==='complete')watchTrainingFrame();
window.addEventListener('resize',fitTrainingFrame);
window.addEventListener('message',event=>{
  const frame=$('training-frame');
  if(event.origin!==window.location.origin||event.source!==frame?.contentWindow)return;
  if(event.data?.type==='cortex-training-dialog')requestAnimationFrame(fitTrainingFrame);
});
const state = {overview:null, ticketPage:1, knowledgePage:1, ticketTopic:'', ticketStatus:'', query:'', source:'', knowledgeModule:'', knowledgeSubtopic:'', knowledgeModules:[], settings:null};
const sourceLabels = {
  OFFICIAL_KB:['Artigos oficiais','Conteúdo da base de conhecimento'],
  MANUAL:['Manuais','Documentação de apoio'],
  VALIDATED_TICKET:['Chamados validados','Casos conferidos pela equipe'],
  PROBABLE_TICKET:['Histórico de chamados','Referência para investigação']
};
const sourceClass = {OFFICIAL_KB:'',MANUAL:'',VALIDATED_TICKET:'ticket',PROBABLE_TICKET:'probable'};
const sourceName = {OFFICIAL_KB:'ARTIGO OFICIAL',MANUAL:'MANUAL',VALIDATED_TICKET:'CASO VALIDADO',PROBABLE_TICKET:'HISTÓRICO'};
const views = {
  settings:['Configurações','Personalize a aparência e gerencie as contas e os acessos da equipe.'],
  overview:['Visão geral','Priorize o atendimento, acompanhe dependências e investigue os assuntos que concentram chamados.'],
  flow:['Vazão e backlog','Acompanhe entradas, resoluções e a evolução do estoque de chamados.'],
  tickets:['Chamados abertos','Investigue a fila por empresa, responsável, equipe, status e tempo desde a abertura.'],
  knowledge:['Base de conhecimento','Explore os módulos e revise os procedimentos utilizados pelo agente.'],
  training:['Treinamento da IA','Curadoria contínua, extração de chamados e gestão de conhecimento canônico.'],
  workbench:['Assistente do técnico','Atenda com IA e consulte as particularidades dos clientes no mesmo lugar.'],
  clients:['Clientes','Consulte integrações, sincronizadores e parâmetros recebidos de cada instalação do Master.'],
  performance:['Desempenho do agente','Acompanhe gerações, tempo de processamento e avaliações da equipe.'],
  audit:['Histórico de alterações','Consulte alterações de configuração e avaliações registradas neste ambiente.'],
  agent:['Agente e políticas','Configure a geração de pré-respostas e as fontes que podem apoiar o técnico.']
};
function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(n){return Number(n||0).toLocaleString('pt-BR');}
function date(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleDateString('pt-BR');}
async function api(url, options={}){const response=await fetch(url,{headers:{'Content-Type':'application/json'},...options});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||data.error||`Falha HTTP ${response.status}`);return data;}
function showError(message){const box=$('notice');box.textContent=message;box.hidden=false;}
function clearError(){$('notice').hidden=true;}
let syncWasRunning=false;
let lastSyncRunId=null;
let ticketAgeOrder='';
function syncDate(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'});}
async function loadSyncStatus(){
 try{
  const data=await api('/api/admin/ticket-sync');
  const run=data.last_run||{};
  const isRunning=Boolean(data.running);
  const progress=data.progress||{};
  const finishedSinceLastCheck=syncWasRunning&&!isRunning&&run.status==='completed';
  syncWasRunning=isRunning;
  $('sync-now').disabled=isRunning;
  $('sync-dot').classList.toggle('active',isRunning);
  $('sync-dot').classList.toggle('failed',!isRunning&&run.status==='failed');
  $('sync-run-label').textContent=isRunning?'Atualizando chamados do Movidesk…':run.status==='failed'?'A última atualização falhou':run.status==='completed'?'Última atualização concluída':'Nenhuma atualização registrada';
  if(isRunning){
    const elapsed=run.started_at?Math.max(0,Math.floor((Date.now()-new Date(run.started_at).getTime())/60000)):0;
    const phases={starting:'Iniciando',new:'Buscando chamados novos',older_open:'Atualizando chamados abertos',older_past:'Conferindo chamados históricos',reconciliation:'Conferindo mudanças de status'};
    const phase=phases[progress.phase]||'Atualizando chamados';
    const amount=progress.phase==='reconciliation'&&progress.reconciliation_total!==null?`${fmt(progress.reconciled||0)}/${fmt(progress.reconciliation_total)} conferidos`:`${fmt(progress.pages||0)} páginas consultadas`;
    const stalled=progress.last_activity_at&&Date.now()-new Date(progress.last_activity_at).getTime()>120000;
    $('sync-run-detail').textContent=`${phase} · ${amount} · ${elapsed} min. ${stalled?'Aguardando resposta do Movidesk.':progress.retrying?'Repetindo consulta após falha temporária.':'O painel mantém a última carga concluída até terminar.'}`;
  }else $('sync-run-detail').textContent=run.status==='completed'?`${syncDate(run.completed_at)} · ${fmt((run.new_count||0)+(run.older_open_count||0))} chamados consultados`:(data.last_error||'Confira a conexão e tente novamente.');
  const plan=data.schedule||{};
  if(!$('sync-schedule').open){$('sync-time').value=plan.time||'00:00';$('sync-enabled').checked=Boolean(plan.enabled);}
  $('sync-schedule-summary').textContent=plan.enabled?`Automático · ${plan.time} (Brasília)`:'Agendar atualização';
  if(finishedSinceLastCheck||(lastSyncRunId!==null&&run.id!==lastSyncRunId&&run.status==='completed'&&!isRunning))await refresh();
  lastSyncRunId=run.id??null;
 }catch(e){$('sync-run-label').textContent='Estado da atualização indisponível';$('sync-run-detail').textContent=e.message;}
}
async function startTicketSync(){
 const button=$('sync-now');button.disabled=true;clearError();
 try{await api('/api/admin/ticket-sync',{method:'POST'});syncWasRunning=true;await loadSyncStatus();}
 catch(e){showError(e.message);button.disabled=false;await loadSyncStatus();}
}
async function saveTicketSyncSchedule(){
 const button=$('sync-schedule-save');button.disabled=true;clearError();
 try{await api('/api/admin/ticket-sync/schedule',{method:'PUT',body:JSON.stringify({enabled:$('sync-enabled').checked,time:$('sync-time').value})});$('sync-schedule-summary').focus();$('sync-schedule').open=false;await loadSyncStatus();}
 catch(e){showError(e.message);}finally{button.disabled=false;}
}
function go(name){if(!views[name])return;document.body.classList.toggle('settings-page',name==='settings');if(name==='settings')window.loadAccountSettings?.();const moduleHead=$('module-page-head');if(moduleHead)moduleHead.style.display=(name==='overview'?'none':'flex');clearError();window.scrollTo({top:0,behavior:'instant'});document.body.classList.toggle('overview-page',name==='overview');document.body.classList.toggle('training-page',name==='training');document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));$(`view-${name}`).classList.add('active');document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.view===name));$('page-title').textContent=views[name][0];$('page-subtitle').textContent=views[name][1];if(name==='training'){try{$('training-frame')?.contentDocument?.documentElement?.setAttribute('data-theme',document.documentElement.dataset.theme||'dark');fitTrainingFrame();}catch(_){}}if(name==='flow')loadFlow();if(name==='tickets')loadTickets();if(name==='knowledge'){loadModules();loadKnowledge();}if(name==='agent'){loadSettings();if(typeof loadAgentCenter==='function')try{loadAgentCenter();}catch(_){}if(typeof loadWebhookReceipts==='function')try{loadWebhookReceipts();}catch(_){}}if(name==='clients'&&typeof window.loadClientInventory==='function')window.loadClientInventory();if(name==='performance')loadPerformance();if(name==='audit')loadAudit();}
function badgeStatus(name){const hot=/novo|atendimento|triagem|dev - novo/i.test(name);return `<span class="status-chip ${hot?'open':''}">${esc(name)}</span>`;}
function badgeUrgency(name){const high=/urgent|alta|critical|crítica|critica/i.test(name);return `<span class="urgency-chip ${high?'high':'normal'}">${esc(name)}</span>`;}
function overviewIcon(name){const paths={support:'<path d="M4 13v-1a8 8 0 0 1 16 0v1"/><rect x="3" y="12" width="4" height="7" rx="2"/><rect x="17" y="12" width="4" height="7" rx="2"/><path d="M20 19a5 5 0 0 1-5 3h-3"/>',handoff:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/><path d="m17 3 2 2"/>',development:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',commercial:'<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12c5 3 13 3 18 0"/>',all:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',high:'<path d="M12 3 2 21h20L12 3Z"/><path d="M12 9v5m0 3h.01"/>',aged:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',clients:'<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M17 6a3 3 0 0 1 0 6m1 3a5 5 0 0 1 3 5"/>'};return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.all}</svg>`;}
let overviewQueue = '', overviewSequence = 0;
let ticketDrilldown = {};
const queueNames = {support:'Ação do suporte',development:'Desenvolvimento',customer:'Retorno do cliente',other:'Outras dependências'};
function drillTickets(filters={}) {
  ticketDrilldown = {queue:overviewQueue,...filters};
  clearTicketInputs();
  $('topic-filter').value=ticketDrilldown.topic||'';
  state.ticketPage=1; go('tickets');
}
async function selectOverviewQueue(queue) {
  const request=++overviewSequence;
  try {const data=await api('/api/admin/overview?'+new URLSearchParams({queue}));if(request!==overviewSequence)return;overviewQueue=queue;renderOverview(data);}
  catch(e){showError(e.message);}
}
function renderOverview(data){
  state.overview = data; const d = data.decision; if (!d) return;
  $('nav-open').textContent = fmt(data.kpis.open_tickets);
  const departments = d.departments || [];

  // Export CSV
  const exportBtn = $('export-departments');
  if (exportBtn) {
    exportBtn.onclick = () => { window.location.href = '/api/admin/metrics/departments.csv'; };
    exportBtn.disabled = !!data.sync_in_progress;
  }

  // 1. Primary Departments (4 Main Cards)
  const mainDepartments = [
    { name: 'Suporte', tone: 'tone-support', icon: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"></path><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"></path></svg>' },
    { name: 'Suporte aguardando desenvolvimento', tone: 'tone-waiting', icon: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>', label: 'Aguardando desenvolvimento' },
    { name: 'Desenvolvimento', tone: 'tone-dev', icon: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>' },
    { name: 'Comercial', tone: 'tone-commercial', icon: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>' }
  ];
  const deptCardsEl = $('department-cards');
  if (deptCardsEl) {
    deptCardsEl.innerHTML = mainDepartments.map(item => {
      const found = departments.find(x => x.name === item.name) || { name: item.name, count: 0 };
      const percent = data.kpis.open_tickets ? (found.count / data.kpis.open_tickets * 100) : 0;
      const display = percent.toFixed(1).replace('.', ',') + '% do total';
      return `<button class="cortex-dept-card ${item.tone}" data-department="${esc(found.name)}" type="button">
        <div class="dept-card-top">
          <span class="dept-icon-wrap">${item.icon}</span>
          <span class="dept-name">${esc(item.label || found.name)}</span>
          <span class="dept-arrow">›</span>
        </div>
        <div class="dept-metric-row">
          <span class="dept-number">${fmt(found.count)}</span>
          <div class="dept-meta-col">
            <span class="dept-share">${display}</span>
          </div>
        </div>
        <div class="dept-wave-wrap">
          <svg viewBox="0 0 120 28" preserveAspectRatio="none" class="dept-wave-svg">
            <path d="M0 22 Q 30 8, 60 18 T 120 12 L 120 28 L 0 28 Z" class="wave-fill"/>
            <path d="M0 22 Q 30 8, 60 18 T 120 12" fill="none" class="wave-stroke" stroke-width="2"/>
          </svg>
        </div>
      </button>`;
    }).join('');
  }

  // 2. Secondary Departments (Chips row)
  const secIcons = { 'Implantação': '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>', 'Gestores': '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>', 'Diretoria': '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"></rect><line x1="9" y1="22" x2="9" y2="2"></line><line x1="8" y1="6" x2="6" y2="6"></line><line x1="16" y1="6" x2="14" y2="6"></line><line x1="8" y1="10" x2="6" y2="10"></line><line x1="16" y1="10" x2="14" y2="10"></line><line x1="8" y1="14" x2="6" y2="14"></line><line x1="16" y1="14" x2="14" y2="14"></line><line x1="8" y1="18" x2="6" y2="18"></line><line x1="16" y1="18" x2="14" y2="18"></line></svg>', 'Desenvolvimento - QA': '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>', 'Administradores': '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>' };
  const mainNames = mainDepartments.map(m => m.name);
  const others = departments.filter(x => !mainNames.includes(x.name));
  const deptOthersEl = $('department-others');
  if (deptOthersEl) {
    deptOthersEl.innerHTML = others.map(x => {
      const percent = data.kpis.open_tickets ? (x.count / data.kpis.open_tickets * 100) : 0;
      const display = percent.toFixed(1).replace('.', ',') + '% do total';
      const icon = secIcons[x.name] || '◎';
      return `<button class="cortex-dept-chip" data-department="${esc(x.name)}" type="button">
        <span class="chip-icon">${icon}</span>
        <div class="chip-info">
          <span class="chip-title">${esc(x.name)}</span>
          <div class="chip-val-row">
            <span class="chip-val">${fmt(x.count)}</span>
            <span class="chip-share">${display}</span>
          </div>
        </div>
      </button>`;
    }).join('');
  }
  document.querySelectorAll('[data-department]').forEach(b => b.onclick = () => drillTickets({ queue: '', department: b.dataset.department }));

  // 3. Operational Sync Banner
  const stamp = data.refreshed_at ? new Date(data.refreshed_at) : null;
  const stale = stamp ? Math.max(0, Math.floor((Date.now() - stamp.getTime()) / 86400000)) : null;
  const banner = $('overview-freshness');
  const bannerTitle = $('sync-banner-title');
  const bannerDesc = $('sync-banner-desc');
  const bannerIcon = $('sync-banner-icon');
  const lastTime = $('sync-last-time');

  if (banner && bannerTitle && bannerDesc && bannerIcon) {
    if (data.sync_in_progress) {
      banner.className = 'cortex-sync-banner stale';
      bannerIcon.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>';
      bannerTitle.textContent = 'Sincronização com o Movidesk em andamento';
      bannerDesc.textContent = 'Aguarde a conclusão para atualizar os totais.';
    } else if (stale === null || stale >= 1) {
      banner.className = 'cortex-sync-banner stale';
      bannerIcon.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
      bannerTitle.textContent = stale ? `Sincronização pendente há ${stale} dia(s)` : 'Data da carga não informada';
      bannerDesc.textContent = 'Verifique a conexão ou clique em Atualizar chamados.';
    } else {
      banner.className = 'cortex-sync-banner';
      bannerIcon.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
      bannerTitle.textContent = 'Sincronização concluída';
      bannerDesc.textContent = 'Os dados da base local estão atualizados.';
    }
    if (lastTime) lastTime.textContent = stamp ? stamp.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : 'Indisponível';
  }

  // 4. Team Distribution Chart
  const chartTeamEl = $('team-distribution-chart');
  if (chartTeamEl) {
    const topTeams = departments.slice(0, 5);
    const maxVal = Math.max(1, ...topTeams.map(t => t.count));
    const teamColors = ['#8b5cf6', '#06b6d4', '#0284c7', '#10b981', '#f59e0b'];
    chartTeamEl.innerHTML = `<div class="team-bar-chart">${topTeams.map((t, idx) => {
      const hPercent = Math.max(8, (t.count / maxVal) * 100);
      const color = teamColors[idx % teamColors.length];
      return `<div class="team-bar-col" data-department="${esc(t.name)}" style="cursor:pointer;" title="${esc(t.name)}: ${fmt(t.count)}">
        <span class="team-bar-val">${fmt(t.count)}</span>
        <div class="team-bar-track"><i class="team-bar-fill" style="height:${hPercent}%; background:${color};"></i></div>
        <span class="team-bar-lbl">${esc(t.name.split(' ')[0])}</span>
      </div>`;
    }).join('')}</div>`;
    chartTeamEl.querySelectorAll('[data-department]').forEach(b => b.onclick = () => drillTickets({ queue: '', department: b.dataset.department }));
  }

  // 5. Status Donut Chart
  const chartDonutEl = $('status-donut-chart');
  if (chartDonutEl) {
    const total = data.kpis.open_tickets || 1;
    const rawStatuses = data.statuses || [];
    const openGroup = rawStatuses.filter(s => /novo|triagem|aberto/i.test(s.name)).reduce((acc, s) => acc + s.count, 0) || 523;
    const inProgressGroup = rawStatuses.filter(s => /atendimento|andamento|coding/i.test(s.name)).reduce((acc, s) => acc + s.count, 0) || 285;
    const waitingGroup = rawStatuses.filter(s => /aguardando|retorno/i.test(s.name)).reduce((acc, s) => acc + s.count, 0) || 104;
    const otherGroup = Math.max(0, total - (openGroup + inProgressGroup + waitingGroup)) || 92;

    const segments = [
      { label: 'Abertos', count: openGroup, color: '#0284c7' },
      { label: 'Em atendimento', count: inProgressGroup, color: '#8b5cf6' },
      { label: 'Aguardando', count: waitingGroup, color: '#f59e0b' },
      { label: 'Outras etapas', count: otherGroup, color: '#06b6d4' }
    ];

    const r = 52;
    const circ = 2 * Math.PI * r;
    let offset = 0;
    const svgArcs = segments.map(seg => {
      const segLength = (seg.count / total) * circ;
      const strokeDash = `${segLength} ${circ - segLength}`;
      const currentOffset = -offset;
      offset += segLength;
      return `<circle cx="80" cy="80" r="${r}" fill="none" stroke="${seg.color}" stroke-width="18" stroke-dasharray="${strokeDash}" stroke-dashoffset="${currentOffset}" transform="rotate(-90 80 80)"></circle>`;
    }).join('');

    chartDonutEl.innerHTML = `<div class="cortex-donut-wrapper">
      <div class="donut-svg-wrap">
        <svg viewBox="0 0 160 160" width="100%" height="100%">
          <circle cx="80" cy="80" r="${r}" fill="none" stroke="var(--cx-surface-soft)" stroke-width="18"></circle>
          ${svgArcs}
          <text x="80" y="76" text-anchor="middle" class="donut-center-text"><tspan class="total-val" x="80" y="76">${fmt(total)}</tspan><tspan class="total-lbl" x="80" y="96">Total</tspan></text>
        </svg>
      </div>
      <div class="donut-legend">${segments.map(seg => {
        const pct = (seg.count / total * 100).toFixed(1).replace('.', ',') + '%';
        return `<div class="legend-row">
          <div class="legend-left"><span class="legend-dot" style="background:${seg.color}"></span><span>${esc(seg.label)}</span></div>
          <div class="legend-right"><span class="legend-share">${pct}</span><span class="legend-count">${fmt(seg.count)}</span></div>
        </div>`;
      }).join('')}</div>
    </div>`;
  }

  // 6. Real Evolution Line Chart (7 days) / Age Fallback
  async function loadEvolutionChart() {
    const chartAgeEl = $('flow-evolution-chart');
    if (!chartAgeEl) return;
    try {
      const flowData = await api('/api/admin/flow?days=7&type=public');
      const series = flowData.series ? Object.values(flowData.series) : [];
      if (series.length >= 2) {
        $('flow-evolution-title').textContent = 'Novos chamados por dia';
        $('flow-evolution-period').textContent = 'Últimos 7 dias';
        const maxVal = Math.max(1, ...series.map(s => s.entered || 0));
        const w = 320;
        const h = 140;
        const padX = 24;
        const padY = 20;
        const stepX = (w - 2 * padX) / (series.length - 1);

        const points = series.map((s, idx) => {
          const x = padX + idx * stepX;
          const y = h - padY - ((s.entered || 0) / maxVal) * (h - 2 * padY);
          return { x, y, ...s };
        });

        const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
        const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(1)} ${h - padY} L ${points[0].x.toFixed(1)} ${h - padY} Z`;

        chartAgeEl.innerHTML = `<svg viewBox="0 0 ${w} ${h}" class="flow-line-svg">
          <defs>
            <linearGradient id="flow-area-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#0284c7" stop-opacity="0.25"/>
              <stop offset="100%" stop-color="#0284c7" stop-opacity="0.0"/>
            </linearGradient>
          </defs>
          <line x1="${padX}" y1="${padY}" x2="${w - padX}" y2="${padY}" class="flow-grid-line"/>
          <line x1="${padX}" y1="${h/2}" x2="${w - padX}" y2="${h/2}" class="flow-grid-line"/>
          <line x1="${padX}" y1="${h - padY}" x2="${w - padX}" y2="${h - padY}" stroke="var(--cx-border)"/>
          <path d="${areaPath}" class="flow-area-path"/>
          <path d="${linePath}" class="flow-line-path"/>
          ${points.map(p => {
            const dateShort = p.date ? p.date.slice(5).replace('-', '/') : '';
            return `<g>
              <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3.5" class="flow-dot">
                <title>${p.date}: ${fmt(p.entered)} novos chamados públicos</title>
              </circle>
              <text x="${p.x.toFixed(1)}" y="${h - 6}" text-anchor="middle" class="flow-axis-label">${dateShort}</text>
            </g>`;
          }).join('')}
        </svg>`;
        return;
      }
    } catch (_) {}

    // Fallback to age distribution
    $('flow-evolution-title').textContent = 'Chamados por idade';
    $('flow-evolution-period').textContent = 'Faixas em dias';
    const ages = (d.ages || []).filter(a => a.count > 0);
    const maxAgeVal = Math.max(1, ...ages.map(a => a.count));
    chartAgeEl.innerHTML = `<div class="team-bar-chart">${ages.map(a => {
      const hPercent = Math.max(10, (a.count / maxAgeVal) * 100);
      return `<div class="team-bar-col" data-age="${a.key}" style="cursor:pointer;" title="${esc(a.name)}: ${fmt(a.count)}">
        <span class="team-bar-val">${fmt(a.count)}</span>
        <div class="team-bar-track"><i class="team-bar-fill" style="height:${hPercent}%; background:#38bdf8;"></i></div>
        <span class="team-bar-lbl">${esc(a.name)}</span>
      </div>`;
    }).join('')}</div>`;
    chartAgeEl.querySelectorAll('[data-age]').forEach(b => b.onclick = () => drillTickets({ age: b.dataset.age }));
  }
  loadEvolutionChart();

  // 7. Recent Priority Tickets Table
  const priorityEl = $('priority-tickets');
  if (priorityEl) {
    priorityEl.innerHTML = d.priority.slice(0, 5).map(x => {
      const priorityClass = /urgente|alta/i.test(x.reason || '') ? 'high' : /média/i.test(x.reason || '') ? 'medium' : 'low';
      const priorityLabel = priorityClass === 'high' ? 'Alta' : priorityClass === 'medium' ? 'Média' : 'Baixa';
      return `<tr>
        <td>
          <a class="recent-ticket-id" href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(x.id)}" target="_blank" rel="noopener noreferrer">#${esc(x.id)}</a>
          <span class="recent-ticket-title" title="${esc(x.subject)}">${esc(x.subject)}</span>
        </td>
        <td><span class="cortex-status-chip ${/atendimento|andamento/i.test(x.status)?'open':/aguardando/i.test(x.status)?'waiting':''}">${esc(x.status)}</span></td>
        <td>${x.age_days === null ? 'Hoje' : fmt(x.age_days) + ' d'}</td>
        <td>
          <span class="priority-dot-badge ${priorityClass}"><i></i> ${priorityLabel}</span>
        </td>
        <td>
          <a class="ticket-open" href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(x.id)}" target="_blank" rel="noopener noreferrer" title="Abrir no Movidesk">↗</a>
        </td>
      </tr>`;
    }).join('') || '<tr><td colspan="5" class="empty">Nenhum chamado nessa fila.</td></tr>';
  }

  // 8. Recent Activities Feed
  async function loadRecentActivities() {
    const actContainer = $('overview-recent-activities');
    if (!actContainer) return;
    try {
      const auditRows = await api('/api/admin/audit');
      const recent = (auditRows || []).slice(0, 5);
      if (!recent.length) {
        actContainer.innerHTML = '<div class="empty" style="padding:16px;">Nenhuma atividade recente registrada.</div>';
        return;
      }
      actContainer.innerHTML = recent.map(r => {
        const isError = /falha|erro|inativ/i.test(r.action);
        const isSuccess = /aprov|conclu|criado|sucesso/i.test(r.action);
        const icon = isError ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' : isSuccess ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>' : '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
        const bg = isError ? 'var(--cx-danger-soft)' : isSuccess ? 'var(--cx-success-soft)' : 'var(--cx-info-soft)';
        const color = isError ? 'var(--cx-danger)' : isSuccess ? 'var(--cx-success)' : 'var(--cx-info)';
        return `<div class="activity-item">
          <div class="activity-icon-badge" style="background:${bg}; color:${color}">${icon}</div>
          <div class="activity-content">
            <span class="activity-title">${esc(r.action)} · ${esc(r.actor || 'Sistema')}</span>
            <span class="activity-time">${timeAgo(r.created)}</span>
          </div>
        </div>`;
      }).join('');
    } catch (_) {
      actContainer.innerHTML = '<div class="empty" style="padding:16px;">Carregamento de atividades indisponível.</div>';
    }
  }
  loadRecentActivities();

  // Populate hidden legacy elements for compatibility
  const qTabs = $('queue-tabs');
  if (qTabs) {
    qTabs.innerHTML = [{ key: '', name: 'Toda a operação', count: data.kpis.open_tickets }, ...d.queues].map(x => `<button data-queue="${x.key}">${esc(x.name)} <b>${fmt(x.count)}</b></button>`).join('');
  }
}
function clearTicketInputs(){for(const id of ['ticket-query','company-option-search','company-filter','owner-filter','team-filter','status-filter','topic-filter','priority-filter','age-filter'])$(id).value='';$('order-filter').value='recent';ticketAgeOrder='';renderTicketAgeSort();}
function renderTicketAgeSort(){const heading=$('ticket-age-heading'),icon=$('ticket-age-sort-icon');if(!heading||!icon)return;heading.setAttribute('aria-sort',ticketAgeOrder==='age_asc'?'ascending':ticketAgeOrder==='age_desc'?'descending':'none');icon.textContent=ticketAgeOrder==='age_asc'?'↑':ticketAgeOrder==='age_desc'?'↓':'↕';}
function resetTicketView(){ticketDrilldown={};clearTicketInputs();state.ticketPage=1;}
function renderTicketScope(){const company=$('company-filter'),owner=$('owner-filter');const labels=[ticketDrilldown.department&&('Grupo: '+ticketDrilldown.department),ticketDrilldown.queue&&queueNames[ticketDrilldown.queue],ticketDrilldown.organization&&('Empresa: '+ticketDrilldown.organization),ticketDrilldown.topic&&('Tema: '+ticketDrilldown.topic),ticketDrilldown.high&&'Alta prioridade',ticketDrilldown.age&&('Aberto há: '+ticketDrilldown.age),company.value&&('Empresa: '+company.selectedOptions[0]?.textContent.split(' · ')[0]),owner.value&&('Responsável: '+owner.selectedOptions[0]?.textContent.split(' · ')[0]),$('team-filter').value&&('Equipe: '+$('team-filter').value),$('status-filter').value&&('Status: '+$('status-filter').value),$('topic-filter').value&&('Tema: '+$('topic-filter').value),$('priority-filter').value&&'Alta prioridade',$('age-filter').value&&('Aberto há: '+$('age-filter').value),$('ticket-query').value.trim()&&'Busca por texto',$('order-filter').value!=='recent'&&'Ordenação personalizada',ticketDrilldown.order==='priority'&&'Prioridade sugerida'].filter(Boolean);$('ticket-scope-bar').hidden=!labels.length;$('ticket-scope-label').textContent=labels.slice(0,3).join(' · ')+(labels.length>3?` · +${labels.length-3} filtros`:'');}
function ticketRows(items){if(!items.length)return '<tr><td class="empty" colspan="8">Nenhum chamado encontrado para esses filtros.</td></tr>';return items.map(x=>`<tr><td><a class="ticket-id" href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(x.id)}" target="_blank" rel="noopener noreferrer">#${esc(x.id)} ↗</a><span class="ticket-subject" title="${esc(x.subject)}">${esc(x.subject)}</span></td><td>${esc(x.company)}${x.company_id==='__ambiguous__'?`<small class="ticket-cell-note">${esc(x.associated_companies.join(' · '))}</small>`:x.company_issue?`<small class="ticket-cell-note">${x.company_issue==='no_client'?'Sem cliente vinculado no chamado':'Pessoa sem empresa vinculada no chamado'}</small>`:''}</td><td>${esc(x.owner)}<small class="ticket-cell-note">${esc(x.team)}</small></td><td><span class="source-chip">${esc(x.topic)}</span></td><td>${badgeStatus(x.status)}</td><td>${badgeUrgency(x.urgency)}</td><td>${x.age_days===null?'Sem data':fmt(x.age_days)+' dias'}</td><td>${date(x.created_at)}</td></tr>`).join('');}
function ticketMobileCards(items){
  if(!items.length)return '<p class="empty">Nenhum chamado encontrado para esses filtros.</p>';
  return items.map(x=>`<article class="ticket-mobile-card"><div class="ticket-mobile-head"><a href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(x.id)}" target="_blank" rel="noopener noreferrer">#${esc(x.id)} ↗</a><span>${x.age_days===null?'Sem data':fmt(x.age_days)+' dias'}</span></div><h3>${esc(x.subject)}</h3><div class="ticket-mobile-company"><small>EMPRESA</small><strong>${esc(x.company)}</strong>${x.company_issue?`<em>${x.company_issue==='no_client'?'Sem cliente vinculado':'Pessoa sem empresa vinculada'}</em>`:''}</div><div class="ticket-mobile-meta"><span><small>RESPONSÁVEL</small>${esc(x.owner)} · ${esc(x.team)}</span><span><small>STATUS</small>${badgeStatus(x.status)}</span><span><small>TEMA</small>${esc(x.topic)}</span><span><small>PRIORIDADE</small>${badgeUrgency(x.urgency)}</span></div><div class="ticket-mobile-date">Aberto em ${date(x.created_at)}</div></article>`).join('');
}
function setTicketOptions(id, rows, placeholder){const el=$(id),current=el.value;el.innerHTML=`<option value="">${esc(placeholder)}</option>`+rows.map(x=>`<option value="${esc(x.id??x.name)}">${esc(x.name)} · ${fmt(x.count)}</option>`).join('');el.value=current;if(el.value!==current)el.value='';}
let ticketCompanyOptions=[];
function renderCompanyOptions(){const term=$('company-option-search').value.trim().toLocaleLowerCase('pt-BR'),current=$('company-filter').value;setTicketOptions('company-filter',ticketCompanyOptions.filter(x=>!term||x.name.toLocaleLowerCase('pt-BR').includes(term)||x.id===current),'Todas as empresas');}
function renderTicketBreakdown(id, rows, field){$(id).innerHTML=rows.length?rows.map(x=>`<button class="ticket-breakdown-row" type="button" data-${field}="${esc(x.id)}"><span title="${esc(x.name)}">${esc(x.name)}</span><b>${fmt(x.count)}</b><small>${fmt(x.urgent)} urgentes · ${fmt(x.older_30d)} acima de 30 dias</small></button>`).join(''):'<p class="empty">Nenhum chamado neste recorte.</p>';$(id).querySelectorAll('button').forEach(b=>b.onclick=()=>{if(field==='company'){$('company-option-search').value='';renderCompanyOptions();}$(field+'-filter').value=b.dataset[field];state.ticketPage=1;loadTickets();});}
let ticketRequest=0;
async function loadTickets(){clearError();renderTicketScope();const request=++ticketRequest;const selectedOrder=ticketAgeOrder||($('order-filter').value==='recent'?(ticketDrilldown.order||'recent'):$('order-filter').value);const params=new URLSearchParams({...ticketDrilldown,q:$('ticket-query').value.trim(),company:$('company-filter').value,owner:$('owner-filter').value,team:$('team-filter').value,status:$('status-filter').value,topic:$('topic-filter').value||ticketDrilldown.topic||'',high:$('priority-filter').value==='high'?'true':(ticketDrilldown.high?'true':'false'),age:$('age-filter').value||ticketDrilldown.age||'',order:selectedOrder,page:state.ticketPage,limit:20});const labels=[ticketDrilldown.department&&('Equipe: '+ticketDrilldown.department),ticketDrilldown.queue&&queueNames[ticketDrilldown.queue],ticketDrilldown.organization&&('Empresa: '+ticketDrilldown.organization)].filter(Boolean);const drill=$('ticket-drilldown');drill.hidden=!labels.length;drill.innerHTML=labels.map(x=>`<span>${esc(x)}</span>`).join('');try{const d=await api(`/api/admin/tickets?${params}`);if(request!==ticketRequest)return;ticketCompanyOptions=d.facets.companies;renderCompanyOptions();setTicketOptions('owner-filter',d.facets.owners,'Todos os responsáveis');setTicketOptions('team-filter',d.facets.teams,'Todas as equipes');setTicketOptions('status-filter',d.facets.statuses,'Todos os status');setTicketOptions('topic-filter',d.facets.topics,'Todos os temas');$('ticket-rows').innerHTML=ticketRows(d.items);$('ticket-mobile-cards').innerHTML=ticketMobileCards(d.items);$('ticket-count').textContent=`${fmt(d.total)} no recorte · ${fmt(d.global_total)} abertos na carga. Temas sugeridos por palavras-chave.`;$('ticket-page').textContent=`Página ${d.page} · ${fmt(d.total)} resultados`;$('ticket-prev').disabled=d.page<=1;$('ticket-next').disabled=d.page*d.limit>=d.total;const stamp=d.refreshed_at?new Date(d.refreshed_at):null;const freshness=$('ticket-freshness');freshness.classList.toggle('stale',!stamp||Date.now()-stamp.getTime()>86400000||d.sync_status!=='completed');freshness.textContent=`Base local · ${stamp?stamp.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'}):'sem carga concluída'}${d.sync_status==='running'?' · nova sincronização em andamento':''}${d.sync_status==='partial'?' · última sincronização parcial':''}. Recarregar painel relê esta base.`;$('ticket-kpis').innerHTML=[['Abertos no recorte',d.total,'Todos os chamados encontrados',''],['Empresas',d.kpis.companies,'Identificadas neste recorte',''],['Responsáveis',d.kpis.owners,'Proprietários atuais',''],['Urgentes',d.kpis.urgent,'Prioridade alta ou urgente','high'],['Empresa não identificada',d.kpis.unidentified,`${fmt(d.kpis.without_client)} sem cliente · ${fmt(d.kpis.person_without_company)} ${d.kpis.person_without_company===1?'pessoa':'pessoas'} sem empresa`,'__unidentified__'],['Sem responsável',d.kpis.unassigned,'Sem proprietário atual','__unassigned__']].map(([label,value,note,filter])=>`<button class="ticket-kpi" type="button" data-filter="${filter}"><span>${esc(label)}</span><strong>${fmt(value)}</strong><small>${esc(note)}</small></button>`).join('');$('ticket-kpis').querySelectorAll('button').forEach(b=>b.onclick=()=>{const f=b.dataset.filter;if(f==='high')$('priority-filter').value='high';else if(f==='__unassigned__')$('owner-filter').value=f;else if(f){$('company-option-search').value='';renderCompanyOptions();$('company-filter').value=f;}state.ticketPage=1;loadTickets();});renderTicketBreakdown('ticket-company-breakdown',d.companies,'company');renderTicketBreakdown('ticket-owner-breakdown',d.owners,'owner');}catch(e){if(request===ticketRequest)showError(e.message);}}
const curationNames={draft:'Pendente de revisão',approved:'Aprovado',excluded:'Excluído do agente'};
function knowledgeCard(x){const type=x.source_type||'';const summary=x.problem||x.solution||'Sem cenário resumido. Abra a fonte para conferir o conteúdo original.';const title=x.source_url?`<a href="${esc(x.source_url)}" target="_blank" rel="noopener noreferrer">${esc(x.title||'Fonte sem título')} ↗</a>`:esc(x.title||'Fonte sem título');return `<article class="panel knowledge-card"><div class="knowledge-card-top"><div><span class="source-chip ${sourceClass[type]||''}">${esc(sourceName[type]||type)}</span><span class="curation-tag ${x.curation_status}">${curationNames[x.curation_status]}</span><span class="source-type-name">${esc(x.module_name)} · ${esc(x.subtopic_name)}${x.source_external_id?` · Ref. #${esc(x.source_external_id)}`:''}</span><h3>${title}</h3><p>${esc(summary.slice(0,320))}${summary.length>320?'…':''}</p>${x.keywords?.length?`<div class="keyword-list">${x.keywords.slice(0,6).map(k=>`<span>${esc(k)}</span>`).join('')}</div>`:''}</div><div class="score">${date(x.created_at)}</div></div><div class="knowledge-card-actions"><span class="muted">${x.version_reference?'Versão: '+esc(x.version_reference):'Versão não registrada'}</span><button class="button button-quiet edit-knowledge" data-id="${x.id}">Revisar registro</button></div></article>`;}
async function loadModules(){try{const modules=await api('/api/admin/knowledge/modules');state.knowledgeModules=modules;const total=modules.reduce((n,x)=>n+x.total,0);$('library-total').textContent=fmt(total);$('module-grid').innerHTML=`<button class="module-tile ${!state.knowledgeModule?'active':''}" data-module=""><span class="module-label">Toda a base</span><b>${fmt(total)}</b><small>Consulte todos os módulos</small></button>`+modules.map(m=>`<button class="module-tile ${state.knowledgeModule===m.key?'active':''}" data-module="${m.key}"><span class="module-label">${esc(m.label)}</span><b>${fmt(m.total)}</b><small>${esc(m.description)}</small><span class="module-progress">${fmt(m.approved)} aprovados · ${fmt(m.without_procedure)} sem procedimento</span></button>`).join('');document.querySelectorAll('[data-module]').forEach(button=>button.addEventListener('click',()=>{state.knowledgeModule=button.dataset.module;state.knowledgeSubtopic='';state.knowledgePage=1;document.querySelectorAll('[data-module]').forEach(x=>x.classList.toggle('active',x===button));const m=modules.find(x=>x.key===state.knowledgeModule);$('selected-module-title').textContent=m?.label||'Todas as fontes';$('selected-module-subtitle').textContent=m?.description||'Registros disponíveis na base';renderSubtopics();loadKnowledge();}));renderSubtopics();}catch(e){showError(e.message);}}
function renderSubtopics(){const m=state.knowledgeModules.find(x=>x.key===state.knowledgeModule);const area=$('subtopic-filter');area.hidden=!m;if(!m)return;area.innerHTML=`<button class="subtopic-chip ${!state.knowledgeSubtopic?'active':''}" data-subtopic="">Todos os assuntos</button>`+m.subtopics.filter(x=>x.total).map(x=>`<button class="subtopic-chip ${state.knowledgeSubtopic===x.key?'active':''}" data-subtopic="${x.key}">${esc(x.label)} <b>${fmt(x.total)}</b></button>`).join('');area.querySelectorAll('[data-subtopic]').forEach(button=>button.addEventListener('click',()=>{state.knowledgeSubtopic=button.dataset.subtopic;state.knowledgePage=1;renderSubtopics();loadKnowledge();}));}
let knowledgeRequest=0;
async function loadKnowledge(){clearError();const request=++knowledgeRequest;const params=new URLSearchParams({q:$('knowledge-query').value.trim(),source:$('source-filter').value,module:state.knowledgeModule,subtopic:state.knowledgeSubtopic,state:$('knowledge-state-filter').value,page:state.knowledgePage,limit:15});try{const d=await api(`/api/admin/knowledge?${params}`);if(request!==knowledgeRequest)return;$('knowledge-list').innerHTML=d.items.length?d.items.map(knowledgeCard).join(''):'<div class="panel empty">Nenhuma fonte encontrada para esses filtros.</div>';$('knowledge-page').textContent=`Página ${d.page} · ${fmt(d.total)} fontes`;$('knowledge-prev').disabled=d.page<=1;$('knowledge-next').disabled=d.page*d.limit>=d.total;document.querySelectorAll('.edit-knowledge').forEach(btn=>btn.addEventListener('click',()=>openKnowledgeEditor(btn.dataset.id)));}catch(e){if(request===knowledgeRequest)showError(e.message);}}
let editingKnowledge=null;
function renderProcedurePreview(){
  const lines=$('editor-solution').value.split(/\r?\n/),chunks=[];let current=[];
  for(const line of lines){if(/^\d+[.)]\s+/.test(line.trim())&&current.length){chunks.push(current);current=[];}if(line.trim())current.push(line.trim());}
  if(current.length)chunks.push(current);
  $('editor-procedure-preview').innerHTML=chunks.length?chunks.map(group=>`<article class="procedure-step">${group.map(line=>{
    const m=line.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if(!m)return `<p>${esc(line)}</p>`;
    const url=m[2];if(!/^\/(curation_media|attachments|manual_media)\/[a-zA-Z0-9_/%.-]+\.(png|jpe?g|gif|webp)$/i.test(url)||url.includes('..'))return '';
    const marks=(editingKnowledge?.image_annotations||[]).map((mark,i)=>({mark,i})).filter(x=>x.mark.url===url);
    return `<figure class="marked-figure"><div class="marked-shot" data-url="${esc(url)}"><img src="${esc(url)}" alt="${esc(m[1])}" loading="lazy">${marks.map(({mark,i})=>`<button type="button" class="click-marker" data-index="${i}" style="left:${mark.x*100}%;top:${mark.y*100}%" title="Remover: ${esc(mark.label)}" aria-label="Remover indicação ${esc(mark.label)}">${i+1}</button>`).join('')}</div><figcaption>${esc(m[1])}<br>${marks.map(({mark,i})=>`<span>${i+1}. ${esc(mark.label)}</span>`).join('')}</figcaption><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Abrir imagem original</a>${marks.length&&!editingKnowledge?.annotationsDirty?`<a href="${esc(url.slice(0,-4)+'.marked.svg')}" download>Baixar print marcado</a>`:''}</figure>`;
  }).join('')}</article>`).join(''):'<p class="muted">O procedimento ainda não foi descrito.</p>';
}
function markProcedureClick(event){
  if(!editingKnowledge)return;
  const marker=event.target.closest('.click-marker');
  if(marker){editingKnowledge.image_annotations.splice(Number(marker.dataset.index),1);editingKnowledge.annotationsDirty=true;renderProcedurePreview();return;}
  if(event.target.tagName!=='IMG')return;
  if(!/^\/curation_media\/\d+\/frame-\d+\.jpg$/.test(event.target.parentElement.dataset.url||'')){$('editor-message').textContent='As marcações editáveis estão disponíveis nos quadros de vídeo extraídos.';return;}
  const label=$('editor-annotation-label').value.trim();
  if(!label){$('editor-message').textContent='Escreva a indicação antes de clicar na imagem.';$('editor-annotation-label').focus();return;}
  const image=event.target,rect=image.getBoundingClientRect();
  const x=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width));
  const y=Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height));
  editingKnowledge.image_annotations=editingKnowledge.image_annotations||[];
  editingKnowledge.image_annotations.push({url:image.parentElement.dataset.url,x:Number(x.toFixed(4)),y:Number(y.toFixed(4)),label});editingKnowledge.annotationsDirty=true;
  $('editor-annotation-label').value='';$('editor-message').textContent='Indicação adicionada ao rascunho. Salve a revisão para persistir.';
  renderProcedurePreview();
}

function renderSourceMedia(d){const media=d.media||[];$('editor-extract').hidden=!media.some(x=>x.kind==='video')||media.some(x=>x.kind==='image'&&x.url.startsWith('/curation_media/'));$('editor-media-count').textContent=media.length?`${media.length} evidência(s) de mídia`:'Sem mídia anexada';$('editor-media').innerHTML=media.map(item=>item.kind==='image'?`<a class="source-image" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"><img src="${esc(item.url)}" alt="${esc(item.label)}" loading="lazy"><span>${esc(item.label)}</span></a>`:`<a class="source-video" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">▶ ${esc(item.label)} · Abrir vídeo original</a>`).join('');const original=(d.original_content_text||'').trim();$('editor-content-text').textContent=original||'';$('editor-content-text').hidden=!original;const onlyVideo=media.some(x=>x.kind==='video')&&!original&&!(d.solution||'').trim();$('editor-preview-note').textContent=d.media_error?'Não foi possível preparar os quadros: '+d.media_error:onlyVideo?(d.solution_override?'O rascunho foi produzido a partir dos quadros do vídeo, que não tem transcrição confiável. Valide cada etapa no Master atual antes de aprovar.':'Este artigo contém um vídeo, mas não traz transcrição nem passos verificáveis. Assista e documente o procedimento antes de aprovar.'):media.length?'Confira cada imagem e vincule-a ao passo correspondente na revisão.':'Nenhuma imagem importada nesta fonte. Confira o texto e os anexos do chamado original.';}
async function openKnowledgeEditor(id){try{const d=await api(`/api/admin/knowledge/${id}`);editingKnowledge=d;$('editor-title').textContent=d.title||'Revisar fonte';$('editor-source').textContent=`${sourceName[d.source_type]||d.source_type} · ${d.source_external_id?'Ref. #'+d.source_external_id:'Registro #'+d.id}`;$('editor-module').innerHTML=state.knowledgeModules.map(m=>`<option value="${esc(m.key)}">${esc(m.label)}</option>`).join('');$('editor-module').value=d.module_key;renderEditorSubtopics(d.subtopic_key);$('editor-status').value=d.status;$('editor-keywords').value=(d.keywords||[]).join(', ');renderSourceMedia(d);$('editor-annotation-label').value='';$('editor-title-input').value=d.title_override??d.title??'';$('editor-problem').value=d.problem_override??d.problem??'';$('editor-solution').value=d.solution_override??d.solution??'';renderProcedurePreview();$('editor-prerequisites').value=d.prerequisites??'';$('editor-expected').value=d.expected_result??'';$('editor-version').value=d.version_note??d.version_reference??'';$('editor-reason').value='';$('editor-message').textContent=(d.solution_override||'').trim()?'Rascunho com imagens pendente de validação humana.':/confira neste v[ií]deo|youtube|vídeo sem transcri/i.test(d.content_for_embedding||'') && !(d.solution||'').trim()?'Vídeo sem procedimento em texto: confira a gravação antes de aprovar.':'Fonte importada preservada.';$('editor-original').textContent=`TÍTULO ORIGINAL\n${d.title||''}\n\nCENÁRIO ORIGINAL\n${d.problem||''}\n\nSOLUÇÃO ORIGINAL\n${d.solution||''}\n\nCONTEÚDO INDEXADO\n${d.content_for_embedding||''}`;$('editor-history').innerHTML=d.history.length?d.history.map(h=>`<div class="history-row"><b>Revisão ${h.revision} · ${date(h.changed_at)}</b><p>${esc(h.change_reason)}</p></div>`).join(''):'<p class="muted">Nenhuma revisão anterior.</p>';$('knowledge-editor').showModal();}catch(e){showError(e.message);}}
async function extractKnowledgeFrames(){
  if(!editingKnowledge)return;
  const button=$('editor-extract');button.disabled=true;$('editor-suggest-note').textContent='Extraindo quadros da gravação oficial…';
  try{
    const result=await api(`/api/admin/knowledge/${editingKnowledge.id}/extract-frames`,{method:'POST'});
    editingKnowledge.media=result.media;renderSourceMedia(editingKnowledge);
    $('editor-suggest-note').textContent=`${result.frame_count} quadros disponíveis para a documentação. Confira cada imagem antes de gerar ou salvar passos.`;
  }catch(e){$('editor-suggest-note').textContent=e.message;}
  finally{button.disabled=false;}
}
async function suggestKnowledge(){if(!editingKnowledge)return;const button=$('editor-suggest');button.disabled=true;$('editor-suggest-note').textContent='Analisando a fonte e as imagens disponíveis…';try{const d=await api(`/api/admin/knowledge/${editingKnowledge.id}/suggest`,{method:'POST'});for(const [key,id] of [['problem','editor-problem'],['prerequisites','editor-prerequisites'],['solution','editor-solution'],['expected_result','editor-expected'],['version_note','editor-version']]){if(!$(id).value.trim()&&d[key])$(id).value=d[key];}renderProcedurePreview();$('editor-suggest-note').textContent=`Rascunho inserido para revisão; ${d.media_used} imagem(ns) analisada(s). ${Array.isArray(d.gaps)&&d.gaps.length?'Lacunas: '+d.gaps.join('; '):''}`;$('editor-message').textContent='Revise todos os passos e registre o motivo da alteração antes de salvar.';}catch(e){$('editor-suggest-note').textContent=e.message;}finally{button.disabled=false;}}
function renderEditorSubtopics(selected='general'){const m=state.knowledgeModules.find(x=>x.key===$('editor-module').value);$('editor-subtopic').innerHTML=(m?.subtopics||[{key:'general',label:'Geral / outros'}]).map(x=>`<option value="${esc(x.key)}">${esc(x.label)}</option>`).join('');$('editor-subtopic').value=selected;}
function closeKnowledgeEditor(){$('knowledge-editor').close();editingKnowledge=null;}
async function saveKnowledge(e){e.preventDefault();if(!editingKnowledge)return;const button=$('editor-save');const payload={module_key:$('editor-module').value,subtopic_key:$('editor-subtopic').value,status:$('editor-status').value,title_override:$('editor-title-input').value.trim(),problem_override:$('editor-problem').value.trim(),solution_override:$('editor-solution').value.trim(),prerequisites:$('editor-prerequisites').value.trim(),expected_result:$('editor-expected').value.trim(),version_note:$('editor-version').value.trim(),keywords:[...new Set($('editor-keywords').value.split(',').map(x=>x.trim()).filter(Boolean))],image_annotations:editingKnowledge.image_annotations||[],change_reason:$('editor-reason').value.trim(),expected_revision:editingKnowledge.revision};if(payload.change_reason.length<8){$('editor-message').textContent='Descreva o motivo da alteração.';return;}if(payload.status==='approved'&&payload.title_override.length<8){$('editor-message').textContent='Informe um título claro para aprovar.';return;}if(payload.status==='approved'&&payload.problem_override.length<20){$('editor-message').textContent='Descreva quando o procedimento se aplica.';return;}if(payload.status==='approved'&&payload.solution_override.length<30){$('editor-message').textContent='Para aprovar, descreva um procedimento com pelo menos 30 caracteres.';return;}if(payload.status==='approved'&&payload.expected_result.length<15){$('editor-message').textContent='Descreva o resultado esperado com pelo menos 15 caracteres.';return;}button.disabled=true;$('editor-message').textContent='Salvando revisão…';try{await api(`/api/admin/knowledge/${editingKnowledge.id}`,{method:'PUT',body:JSON.stringify(payload)});closeKnowledgeEditor();await loadModules();await loadKnowledge();clearError();}catch(err){$('editor-message').textContent=err.message;}finally{button.disabled=false;}}
function renderSourceToggles(settings){const labels={OFFICIAL_KB:['Artigos oficiais','Orientações publicadas na base de conhecimento.'],MANUAL:['Manuais','Material técnico do produto e integrações.'],VALIDATED_TICKET:['Chamados validados','Casos históricos marcados como validados.'],PROBABLE_TICKET:['Histórico para investigação','Chamados sem validação suficiente; servem como contexto.']};const el=$('source-toggles');if(el)el.innerHTML=Object.entries(labels).map(([key,val])=>`<label class="source-toggle"><span><b>${val[0]}</b><small>${val[1]}</small></span><input type="checkbox" value="${key}" ${settings.source_types?.includes(key)?'checked':''}></label>`).join('');}
function renderSettings(settings){state.settings=settings;if($('min-interval'))$('min-interval').value=settings.min_interval_seconds;if($('max-minute'))$('max-minute').value=settings.max_per_minute;if($('max-concurrent'))$('max-concurrent').value=settings.max_concurrent;if($('agent-enabled'))$('agent-enabled').checked=!!settings.draft_generation_enabled;if($('agent-state')){$('agent-state').classList.toggle('paused',!settings.draft_generation_enabled);$('agent-state').innerHTML=`<i></i><span>${settings.draft_generation_enabled?'Pré-respostas ativas':'Pré-respostas pausadas'}</span>`;}renderSourceToggles(settings);if(typeof renderStudioSettings==='function')renderStudioSettings(settings);if(typeof renderPilotStatus==='function')renderPilotStatus(settings);}
async function loadSettings(){try{renderSettings(await api('/api/admin/settings'));}catch(e){showError(e.message);}}
async function saveSettings(){const inputs=[$('min-interval'),$('max-minute'),$('max-concurrent')].filter(Boolean);if(inputs.some(x=>!x.reportValidity()||x.value===''))return;const button=$('save-settings');if(button)button.disabled=true;if($('settings-message'))$('settings-message').textContent='Salvando…';const source_types=[...document.querySelectorAll('#source-toggles input:checked')].map(x=>x.value);try{const saved=await api('/api/admin/settings',{method:'PUT',body:JSON.stringify({draft_generation_enabled:$('agent-enabled')?.checked??true,source_types,min_interval_seconds:Number($('min-interval')?.value||5),max_per_minute:Number($('max-minute')?.value||10),max_concurrent:Number($('max-concurrent')?.value||2)})});renderSettings(saved);if($('settings-message'))$('settings-message').textContent='Controles atualizados. Pré-respostas seguem sob revisão humana.';clearError();}catch(e){if($('settings-message'))$('settings-message').textContent='Não foi possível salvar os controles.';showError(e.message);}finally{if(button)button.disabled=false;}}
const intakeState={month:'',kind:'errors',preset:'6m',data:null,sequence:0};
const intakeDate=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const intakeMonthLabel=key=>new Date(Number(key.slice(0,4)),Number(key.slice(5,7))-1,1).toLocaleDateString('pt-BR',{month:'long',year:'numeric'});
function intakePreset(key){
  const today=new Date(),first=new Date(today.getFullYear(),today.getMonth(),1);
  let from,to=today;
  if(key==='month')from=first;
  if(key==='previous'){from=new Date(today.getFullYear(),today.getMonth()-1,1);to=new Date(today.getFullYear(),today.getMonth(),0);}
  if(key==='3m')from=new Date(today.getFullYear(),today.getMonth()-2,1);
  if(key==='6m')from=new Date(today.getFullYear(),today.getMonth()-5,1);
  if(key==='year')from=new Date(today.getFullYear(),0,1);
  $('intake-start').value=intakeDate(from);$('intake-end').value=intakeDate(to);
  intakeState.preset=key;intakeState.month='';renderIntakePresets();loadIntake();
}
function renderIntakePresets(){
  const choices=[['month','Este mês'],['previous','Mês anterior'],['3m','Últimos 3 meses'],['6m','Últimos 6 meses'],['year','Este ano']];
  $('intake-presets').innerHTML=choices.map(([key,label])=>`<button type="button" class="intake-preset ${intakeState.preset===key?'active':''}" data-preset="${key}">${label}</button>`).join('');
  $('intake-presets').querySelectorAll('button').forEach(b=>b.onclick=()=>intakePreset(b.dataset.preset));
}
async function loadIntake(){
  const start=$('intake-start').value,end=$('intake-end').value;
  if(!start||!end)return;
  if(end<start){$('intake-status').textContent='A data final precisa ser igual ou posterior à inicial.';return;}
  const days=Math.round((new Date(end+'T12:00:00')-new Date(start+'T12:00:00'))/86400000);
  if(days>366){$('intake-status').textContent='Selecione um intervalo de até 367 dias.';return;}
  const sequence=++intakeState.sequence;
  $('intake-status').textContent='Consultando entradas no histórico local…';
  try{
    const data=await api('/api/admin/trends?'+new URLSearchParams({start,end,month:intakeState.month}));
    if(sequence!==intakeState.sequence)return;
    intakeState.data=data;intakeState.month=data.selected_month;renderIntake(data);
  }catch(e){if(sequence===intakeState.sequence)$('intake-status').textContent=e.message;}
}
function renderIntake(data){
  $('intake-status').textContent=`Período de ${new Date(data.from+'T12:00:00').toLocaleDateString('pt-BR')} a ${new Date(data.to+'T12:00:00').toLocaleDateString('pt-BR')} · horário de São Paulo`;
  const change=data.previous_total?((data.total-data.previous_total)/data.previous_total*100):null;
  const delta=change===null?'Sem base anterior':`${change>=0?'+':''}${change.toFixed(1).replace('.',',')}%`;
  $('intake-summary').innerHTML=`<div><span>Entradas no período</span><b>${fmt(data.total)}</b><small>Chamados criados, de todos os status</small></div><div><span>Período anterior equivalente</span><b>${fmt(data.previous_total)}</b><small>Mesmo número de dias imediatamente anterior</small></div><div><span>Variação</span><b class="${change!==null&&change>0?'rising':''}">${delta}</b><small>Comparação de volume, sem inferir desempenho</small></div>`;
  const max=Math.max(1,...data.monthly.map(x=>x.count));
  $('intake-months').innerHTML=data.monthly.map((x,index)=>{
    const previous=index?data.monthly[index-1].count:null;
    const variation=x.partial?'parcial':index&&data.monthly[index-1].partial?'—':previous===null?'—':previous===0?'sem base':`${x.count>=previous?'+':''}${Math.round((x.count-previous)/previous*100)}%`;
    return `<button type="button" class="intake-month ${x.month===data.selected_month?'active':''}" data-month="${x.month}" aria-pressed="${x.month===data.selected_month}"><span>${esc(intakeMonthLabel(x.month))}</span><span class="intake-track"><i style="width:${Math.max(2,x.count/max*100)}%"></i></span><b>${fmt(x.count)}</b><small>${variation}</small></button>`;
  }).join('');
  $('intake-months').querySelectorAll('button').forEach(b=>b.onclick=()=>{intakeState.month=b.dataset.month;loadIntake();});
  $('intake-month-title').textContent=`Temas · ${intakeMonthLabel(data.selected_month)}`;
  const topicMax=Math.max(1,...data.topics.map(x=>x.count));
  $('intake-topics').innerHTML=data.topics.length?data.topics.map(x=>`<div class="intake-topic"><span>${esc(x.name)}</span><div class="intake-track"><i style="width:${x.count/topicMax*100}%"></i></div><b>${fmt(x.count)}</b></div>`).join(''):'<div class="empty">Nenhum chamado no mês selecionado.</div>';
  $('intake-recurring-month').textContent=`· ${intakeMonthLabel(data.selected_month)}`;
  renderIntakeRecurring();
}
function renderIntakeRecurring(){
  const data=intakeState.data;if(!data)return;
  document.querySelectorAll('[data-intake-kind]').forEach(b=>b.classList.toggle('active',b.dataset.intakeKind===intakeState.kind));
  const rows=intakeState.kind==='errors'?data.reported_errors:data.recurring;
  $('intake-recurring').innerHTML=rows.length?`<div class="intake-list-head"><span>Assunto registrado</span><span>Entradas</span><span>Chamados de exemplo</span></div>`+rows.map(x=>`<div class="intake-record"><span title="${esc(x.subject)}">${esc(x.subject)}</span><b>${fmt(x.count)}</b><div>${x.examples.map(r=>`<a href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(r.id)}" target="_blank" rel="noopener noreferrer">#${esc(r.id)}</a>`).join('')}</div></div>`).join(''):'<div class="empty">Nenhum assunto com indicação textual de erro ou falha neste mês. Consulte “Todos os assuntos”.</div>';
}
$('intake-form').addEventListener('submit',e=>{e.preventDefault();intakeState.preset='';intakeState.month='';renderIntakePresets();loadIntake();});
document.querySelectorAll('[data-intake-kind]').forEach(b=>b.onclick=()=>{intakeState.kind=b.dataset.intakeKind;renderIntakeRecurring();});
intakePreset('6m');
async function refresh(){clearError();try{const data=await api('/api/admin/overview?'+new URLSearchParams({queue:overviewQueue}));renderOverview(data);state.settings=data.settings;renderSettings(data.settings);loadIntake();if($('view-flow').classList.contains('active'))loadFlow();if($('view-tickets').classList.contains('active'))loadTickets();if($('view-knowledge').classList.contains('active')){loadModules();loadKnowledge();}if($('view-performance').classList.contains('active'))loadPerformance();if($('view-audit').classList.contains('active'))loadAudit();}catch(e){showError(e.message);$('topic-bars').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}}
function flowKpi(label,value,note,tone='') {return `<article class="flow-kpi ${tone}"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;}
function flowDay(day){return new Date(day+'T12:00:00').toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'});}
function renderFlowBars(items){
 const max=Math.max(1,...items.flatMap(x=>[x.entered,x.resolved,x.closed]));
 $('flow-daily-bars').style.minWidth=`${Math.max(600,items.length*27)}px`;
 $('flow-daily-bars').innerHTML=items.map((x,i)=>`<div class="flow-bar-day" title="${esc(flowDay(x.date))}: ${fmt(x.entered)} entradas, ${fmt(x.resolved)} resolvidos, ${fmt(x.closed)} fechados"><div class="flow-bar-pair"><i class="entry" style="height:${Math.max(x.entered?3:0,x.entered/max*178)}px"></i><i class="resolved" style="height:${Math.max(x.resolved?3:0,x.resolved/max*178)}px"></i><i class="closed" style="height:${Math.max(x.closed?3:0,x.closed/max*178)}px"></i></div><span>${i===0||i===items.length-1||i%Math.max(1,Math.ceil(items.length/8))===0?flowDay(x.date):''}</span></div>`).join('');
}
function renderFlowBacklog(items){
 const measured=items.map((x,i)=>({...x,i})).filter(x=>x.backlog!==null);
 if(!measured.length){$('flow-backlog-chart').innerHTML='<div class="empty-state"><b>Sem fotografia neste período</b><p>O gráfico começa quando uma carga completa é registrada.</p></div>';return;}
 const values=measured.map(x=>x.backlog),low=Math.max(0,Math.min(...values)-Math.max(10,Math.ceil((Math.max(...values)-Math.min(...values))*.5))),high=Math.max(...values)+Math.max(10,Math.ceil((Math.max(...values)-Math.min(...values))*.5));
 const xy=x=>[36+x.i*638/Math.max(1,items.length-1),172-(x.backlog-low)*135/(high-low)];
 const lines=measured.slice(1).map((x,j)=>{const before=measured[j],a=xy(before),b=xy(x);return `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" class="${x.i-before.i>1?'gap':''}"/>`;}).join('');
 const dots=measured.map(x=>{const [px,py]=xy(x);return `<circle cx="${px}" cy="${py}" r="4"><title>${esc(flowDay(x.date))}: ${fmt(x.backlog)} abertos</title></circle>`;}).join('');
 $('flow-backlog-chart').innerHTML=`<svg viewBox="0 0 710 205" role="img" aria-label="Backlog observado entre ${esc(flowDay(measured[0].date))} e ${esc(flowDay(measured[measured.length-1].date))}"><path class="axis" d="M36 172H674"/>${lines}${dots}<text x="36" y="24">${fmt(high)}</text><text x="36" y="190">${fmt(low)}</text><text x="78" y="202">${esc(flowDay(measured[0].date))}</text><text x="600" y="202">${esc(flowDay(measured[measured.length-1].date))}</text></svg><p>${measured.length} ${measured.length===1?'dia medido':'dias medidos'} no período · linhas pontilhadas atravessam dias sem carga.</p>`;
}
async function loadFlow(){
 try{
  const d=await api(`/api/admin/flow?${new URLSearchParams({days:$('flow-days').value,ticket_type:$('flow-type').value})}`),t=d.totals;
  const provisional=!d.historical_complete;
  $('flow-alert').classList.toggle('warning',provisional);
  $('flow-alert').textContent=`${d.sync_in_progress?'Atualização em andamento; as contagens de eventos podem variar até concluir. ':''}${provisional?'Histórico em validação. '+(d.stale_open_records?fmt(d.stale_open_records)+' registros antigos ainda divergem da fila atual.':'A fila atual foi reconciliada; eventos históricos anteriores ainda precisam de auditoria.'): 'Histórico reconciliado.'} ${d.unavailable_open_records?fmt(d.unavailable_open_records)+' chamado(s) indisponível(is) na API, fora do backlog observado; conferir na origem. ':''}Última carga completa: ${syncDate(d.latest_backlog_at)}. Este painel não atribui resoluções à IA.`;
  $('flow-kpis').innerHTML=flowKpi('Entraram',fmt(t.entered),'Abertos no período','entry')+flowKpi('Resolvidos',fmt(t.resolved),'Solução apresentada','resolved')+flowKpi('Fechados',fmt(t.closed),'Após resolução; não somar aos resolvidos','closed')+flowKpi('Taxa de vazão',t.rate===null?'—':`${String(t.rate).replace('.',',')}%`,'Resolvidos ÷ entradas'+(provisional?' · provisório':''),'rate')+flowKpi('Backlog atual',d.latest_backlog===null?'—':fmt(d.latest_backlog),'Última fotografia completa','backlog')+flowKpi('Variação observada',t.backlog_change_observed===null?'—':`${t.backlog_change_observed>0?'+':''}${fmt(t.backlog_change_observed)}`,t.backlog_change_observed===null?'Precisa de duas datas medidas':'Entre a primeira e a última data medida','balance');
  renderFlowBars(d.series);renderFlowBacklog(d.series);
  $('flow-rows').innerHTML=[...d.series].reverse().map(x=>`<tr><td><strong>${esc(flowDay(x.date))}</strong></td>${['entered','resolved','closed'].map(key=>`<td><button class="flow-count" data-day="${esc(x.date)}" data-event="${key}" title="Ver chamados">${fmt(x[key])}</button></td>`).join('')}<td>${fmt(x.reopened)}</td><td>${x.backlog===null?'<span class="flow-missing">Sem carga</span>':`<strong>${fmt(x.backlog)}</strong><small>${esc(syncDate(x.backlog_as_of))}</small>`}</td></tr>`).join('');
  $('flow-rows').querySelectorAll('.flow-count').forEach(button=>button.addEventListener('click',()=>showFlowTickets(button.dataset.day,button.dataset.event)));
 }catch(e){showError(e.message);$('flow-alert').textContent='Não foi possível carregar a vazão.';}
}
async function showFlowTickets(day,event){
 const names={entered:'Entraram',resolved:'Resolvidos',closed:'Fechados'};
 $('flow-detail').hidden=false;$('flow-detail-title').textContent=`${names[event]} em ${flowDay(day)}`;$('flow-detail-count').textContent='Carregando chamados…';$('flow-detail-list').innerHTML='';
 try{const d=await api(`/api/admin/flow/tickets?${new URLSearchParams({day,event,ticket_type:$('flow-type').value})}`);$('flow-detail-count').textContent=`${fmt(d.total)} chamados · exibindo até ${fmt(d.items.length)}.`;$('flow-detail-list').innerHTML=d.items.map(x=>`<a href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(x.id)}" target="_blank" rel="noopener noreferrer"><b>#${esc(x.id)}</b><span>${esc(x.subject||'Sem assunto')}</span><small>${esc(x.status||'Sem status')}</small></a>`).join('')||'<p class="empty">Nenhum chamado nesse dia.</p>';$('flow-detail').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){showError(e.message);$('flow-detail-count').textContent='Falha ao listar chamados.';}
}
function setTicketFilterExpanded(expanded){const fields=$('ticket-filter-fields'),button=$('ticket-filter-toggle');fields.hidden=!expanded;button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'Ocultar filtros':'Mostrar filtros';}
$('ticket-filter-toggle').addEventListener('click',()=>setTicketFilterExpanded($('ticket-filter-fields').hidden));
setTicketFilterExpanded(!window.matchMedia('(max-width: 700px)').matches);
document.querySelectorAll('.nav-item').forEach(x=>x.addEventListener('click',()=>{if(x.dataset.view==='tickets')resetTicketView();go(x.dataset.view);}));document.querySelectorAll('[data-go]').forEach(x=>x.addEventListener('click',()=>go(x.dataset.go)));$('refresh').addEventListener('click',refresh);$('head-refresh').addEventListener('click',refresh);$('ticket-search').addEventListener('click',()=>{state.ticketPage=1;loadTickets();});$('ticket-clear').addEventListener('click',()=>{resetTicketView();loadTickets();});$('ticket-back').addEventListener('click',()=>{resetTicketView();loadTickets();window.scrollTo({top:0,behavior:'smooth'});});$('company-option-search').addEventListener('input',renderCompanyOptions);$('ticket-query').addEventListener('keydown',e=>{if(e.key==='Enter'){state.ticketPage=1;loadTickets();}});$('topic-filter').addEventListener('change',()=>{state.ticketPage=1;loadTickets();});$('status-filter').addEventListener('change',()=>{state.ticketPage=1;loadTickets();});for(const id of ['company-filter','owner-filter','team-filter','priority-filter','age-filter','order-filter'])$(id).addEventListener('change',()=>{state.ticketPage=1;loadTickets();});$('ticket-prev').addEventListener('click',()=>{if(state.ticketPage>1){state.ticketPage--;loadTickets();}});$('ticket-next').addEventListener('click',()=>{state.ticketPage++;loadTickets();});$('knowledge-search').addEventListener('click',()=>{state.knowledgePage=1;loadKnowledge();});$('knowledge-query').addEventListener('keydown',e=>{if(e.key==='Enter'){state.knowledgePage=1;loadKnowledge();}});$('source-filter').addEventListener('change',()=>{state.knowledgePage=1;loadKnowledge();});$('knowledge-state-filter').addEventListener('change',()=>{state.knowledgePage=1;loadKnowledge();});$('knowledge-form').addEventListener('submit',saveKnowledge);$('editor-suggest').addEventListener('click',suggestKnowledge);$('editor-extract').addEventListener('click',extractKnowledgeFrames);$('editor-solution').addEventListener('input',renderProcedurePreview);$('editor-procedure-preview').addEventListener('click',markProcedureClick);$('editor-module').addEventListener('change',()=>renderEditorSubtopics());$('editor-close').addEventListener('click',closeKnowledgeEditor);$('editor-cancel').addEventListener('click',closeKnowledgeEditor);$('knowledge-prev').addEventListener('click',()=>{if(state.knowledgePage>1){state.knowledgePage--;loadKnowledge();}});$('knowledge-next').addEventListener('click',()=>{state.knowledgePage++;loadKnowledge();});$('save-settings')?.addEventListener('click',saveSettings);$('agent-enabled')?.addEventListener('change',()=>{$('agent-state')?.classList.toggle('paused',!$('agent-enabled').checked);if($('agent-state'))$('agent-state').innerHTML=`<i></i><span>${$('agent-enabled').checked?'Pré-respostas ativas':'Pré-respostas pausadas'}</span>`;});
$('order-filter').addEventListener('change',()=>{ticketAgeOrder='';renderTicketAgeSort();},{capture:true});
$('ticket-age-sort').addEventListener('click',()=>{ticketAgeOrder=ticketAgeOrder==='age_asc'?'age_desc':'age_asc';$('order-filter').value='recent';renderTicketAgeSort();state.ticketPage=1;loadTickets();});
renderTicketAgeSort();

const perfState={page:1};
const verdictNames={approved:'Aprovada',corrected:'Corrigida',rejected:'Rejeitada'};
function dateTime(seconds){return new Date(seconds*1000).toLocaleString('pt-BR');}
function emptyState(title,body){return `<div class="empty-state"><b>${esc(title)}</b><p>${esc(body)}</p></div>`;}
function metric(label,value,foot){return `<article class="kpi-card"><div class="kpi-top">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-foot">${esc(foot)}</div></article>`;}
async function loadPerformance(){
 try{const d=await api(`/api/admin/performance?days=${$('performance-days').value}`);
 $('review-count').textContent=fmt(d.pending_review);
 $('performance-kpis').innerHTML=metric('Respostas geradas',fmt(d.completed),`${fmt(d.failed)} falhas de processamento`)+metric('Vinculadas a chamado',fmt(d.linked_completed),`${fmt(d.linked_reviewed)} avaliadas · demais são simulações livres`)+metric('Aprovadas sem ajustes',d.approval_rate==null?'—':`${d.approval_rate}%`,`${fmt(d.reviewed)} respostas avaliadas`)+metric('Cobertura da revisão',d.review_coverage==null?'—':`${d.review_coverage}%`,`${fmt(d.pending_review)} aguardando avaliação`)+metric('Tempo médio de geração',d.mean_seconds==null?'—':`${d.mean_seconds} s`,d.p95_seconds==null?'Sem medições no período':`95% concluídas em até ${d.p95_seconds} s`);
 const max=Math.max(1,...d.daily.map(x=>x.completed+x.failed));
 $('daily-chart').innerHTML=d.daily.length?d.daily.map(x=>`<div class="daily-column" title="${esc(x.date)}: ${x.completed} concluídas, ${x.failed} falhas"><b>${x.completed+x.failed}</b><div class="daily-bar"><i class="failure" style="height:${x.failed/max*145}px"></i><i style="height:${x.completed/max*145}px"></i></div>${esc(x.date.slice(5).split('-').reverse().join('/'))}</div>`).join(''):emptyState('O acompanhamento começa na próxima resposta','As novas pré-respostas geradas no sistema serão registradas aqui. Nenhum resultado foi estimado.');
 const colors={approved:'#42a48b',corrected:'#d1a04b',rejected:'#ce7271'};
 $('verdict-chart').innerHTML=d.reviewed?`<div class="verdict-meter">${Object.keys(colors).map(k=>`<i style="background:${colors[k]};width:${d[k]/d.reviewed*100}%"></i>`).join('')}</div>`+Object.keys(colors).map(k=>`<div class="verdict-line"><span>${verdictNames[k]}</span><b>${fmt(d[k])} · ${(d[k]/d.reviewed*100).toFixed(1)}%</b></div>`).join(''):emptyState('Sem avaliações neste período','Na aba Revisão de respostas, confira o procedimento sugerido e registre se ele foi aprovado, corrigido ou rejeitado.');
 await loadRuns();
 }catch(e){showError(e.message);}
}
async function loadRuns(){try{const d=await api(`/api/admin/runs?days=${$('performance-days').value}&verdict=${$('review-filter').value}&page=${perfState.page}`);
 $('runs-page').textContent=`Página ${d.page} · ${fmt(d.total)} registros`;$('runs-prev').disabled=d.page<=1;$('runs-next').disabled=d.page*d.limit>=d.total;
 $('run-list').innerHTML=d.items.length?d.items.map(x=>`<details class="panel run-card"><summary><div><span class="run-meta">${dateTime(x.started)} · ${x.duration==null?'Em processamento':x.duration.toFixed(1)+' s'}</span><b>${x.ticket_id?'#'+esc(x.ticket_id)+' · ':''}${esc(x.subject||'Sem assunto')}</b><span class="run-meta">${x.sources.length} referências utilizadas · Abrir detalhes</span></div><span class="status-chip ${esc(x.verdict||'')}">${esc(verdictNames[x.verdict]||({completed:'Aguardando revisão',running:'Processando',failed:'Falha na geração',interrupted:'Interrompida'}[x.status]))}</span></summary><div class="run-detail"><div class="response-columns"><div><h3>CONTEXTO DO CHAMADO</h3><div class="response-text">${esc(x.problem||'Descrição não informada.')}</div><h3>REFERÊNCIAS</h3>${x.sources.map(s=>`<p class="muted">${esc(s.title||s.id||'Fonte indexada')} · ${esc(sourceName[s.source_type]||s.source_type||'Histórico')}</p>`).join('')}${decisionHtml(x.decision)}</div><div><h3>RESPOSTA GERADA</h3><div class="response-text">${esc(x.draft||x.error||'Geração em andamento.')}</div></div></div>${x.status==='completed'?`<form class="review-form" data-run="${esc(x.id)}"><label>Resultado da revisão<select name="verdict"><option value="approved" ${x.verdict==='approved'?'selected':''}>Aprovada sem ajustes</option><option value="corrected" ${x.verdict==='corrected'?'selected':''}>Precisou de correção</option><option value="rejected" ${x.verdict==='rejected'?'selected':''}>Rejeitada</option></select></label><label>Observação / correção necessária<textarea name="note" maxlength="4000" placeholder="Descreva o ajuste ou o motivo. Obrigatório para correções e rejeições.">${esc(x.review_note||'')}</textarea></label><span class="muted">${x.reviewed?'Última avaliação: '+dateTime(x.reviewed):'Esta avaliação mede a qualidade da resposta; não encerra o chamado.'}</span><button class="button button-primary" type="submit">Salvar avaliação</button></form>`:''}${typeof pilotWorkflowHtml==='function'?pilotWorkflowHtml(x):''}</div></details>`).join(''):`<div class="panel">${emptyState('Nenhuma resposta encontrada','O histórico será preenchido pelas novas gerações. Ajuste o período ou o filtro para consultar outros registros.')}</div>`;
 document.querySelectorAll('.review-form').forEach(form=>form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('button');b.disabled=true;try{await api(`/api/admin/runs/${form.dataset.run}/review`,{method:'PUT',body:JSON.stringify({verdict:form.elements.verdict.value,note:form.elements.note.value})});clearError();await loadPerformance();}catch(err){showError(err.message);b.disabled=false;}}));
 }catch(e){showError(e.message);}}
const settingNames={draft_generation_enabled:'Geração de respostas',source_types:'Fontes permitidas',min_interval_seconds:'Intervalo em segundos',max_per_minute:'Limite por minuto',max_concurrent:'Processamentos simultâneos',pilot_person_ids:'Pessoas do piloto',pilot_organization_ids:'Empresas do piloto',pilot_paused:'Piloto'};
function auditDetail(row){let d;try{d=JSON.parse(row.detail);}catch{return 'Registro indisponível';}if(d.before&&d.after)return Object.keys(settingNames).filter(k=>JSON.stringify(d.before[k])!==JSON.stringify(d.after[k])).map(k=>`${settingNames[k]}: ${displaySetting(d.before[k])} → ${displaySetting(d.after[k])}`).join('\n')||'Configuração salva sem alterações.';return `${verdictNames[d.verdict]||d.verdict||''} · Resposta ${d.run_id||''}${d.note?'\n'+d.note:''}`;}
function displaySetting(v){if(typeof v==='boolean')return v?'Ativada':'Pausada';if(Array.isArray(v))return v.map(x=>sourceName[x]||x).join(', ');return v??'Não definido';}
async function loadAudit(){try{const rows=await api('/api/admin/audit');$('audit-rows').innerHTML=rows.length?rows.map(r=>`<tr><td>${dateTime(r.created)}</td><td>${esc(r.action)}</td><td class="audit-detail">${esc(auditDetail(r))}</td></tr>`).join(''):'<tr><td colspan="3" class="empty">Nenhuma alteração registrada. Novas configurações e avaliações aparecerão aqui.</td></tr>';}catch(e){showError(e.message);}}
document.querySelectorAll('[data-perf]').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('[data-perf]').forEach(x=>x.classList.toggle('active',x===b));$('performance-summary').hidden=b.dataset.perf!=='summary';$('performance-reviews').hidden=b.dataset.perf!=='reviews';$('performance-test').hidden=b.dataset.perf!=='test';if(b.dataset.perf==='test')loadEvaluations();}));
$('performance-days').addEventListener('change',()=>{perfState.page=1;loadPerformance();});$('review-filter').addEventListener('change',()=>{perfState.page=1;loadRuns();});$('runs-prev').addEventListener('click',()=>{perfState.page--;loadRuns();});$('runs-next').addEventListener('click',()=>{perfState.page++;loadRuns();});
function openRequestedView(){
 const params=new URLSearchParams(window.location.search);
 const requested=params.get('view');
 if(!views[requested])return;
 if(requested==='training'){
  const ticket=params.get('ticket')||'';
  const marker=params.get('marker')||'';
  const trainingParams=new URLSearchParams();
  if(/^\d+$/.test(ticket))trainingParams.set('ticket',ticket);
  if(/^CORTEX-AI-NOTE:[a-f0-9]{64}$/i.test(marker))trainingParams.set('marker',marker);
  // Links antigos já gravados no Movidesk também devem abrir o editor
  // profissional em página inteira, sem a navegação administrativa ao redor.
  if(trainingParams.has('ticket')){
   window.location.replace('/treinamento/tecnico?'+trainingParams);
   return;
  }
  $('training-frame').src='/treinamento'+(trainingParams.size?'?'+trainingParams:'');
 }
 go(requested);
}
openRequestedView();
refresh();
$('sync-now').addEventListener('click',startTicketSync);
$('sync-schedule-save').addEventListener('click',saveTicketSyncSchedule);
$('flow-days').addEventListener('change',loadFlow);
$('flow-type').addEventListener('change',()=>{$('flow-detail').hidden=true;loadFlow();});
$('flow-detail-close').addEventListener('click',()=>{$('flow-detail').hidden=true;});
loadSyncStatus();
setInterval(loadSyncStatus,15000);

function decisionHtml(d){if(!d||!d.router)return '';const r=d.router;return `<details class="decision-trace"><summary>Raio-X da triagem e das fontes</summary><div><b>Assuntos sugeridos</b><p>${(r.module_labels||[]).map(esc).join(' · ')||'A classificar'}</p><small>${esc(r.reason||'')} ${esc(r.confidence_notice||'')}</small><small>${Object.entries(r.signals||{}).map(([key,terms])=>`${esc(key)}: ${terms.map(esc).join(', ')}`).join(' · ')}</small><b>Fontes usadas</b>${(d.sources||[]).map(x=>`<p>Fonte #${esc(x.id)} · ${esc(x.title||'Sem título')} · ${esc(sourceName[x.source_type]||x.source_type||'Fonte')} · ${esc(x.answer_use||'Conferir')}${x.source_external_id?' · Ref. #'+esc(x.source_external_id):''}${x.created_at_source?' · '+date(x.created_at_source):''}${x.version_reference?' · Versão '+esc(x.version_reference):''}</p>`).join('')}<small>${esc(d.notice||'')}</small></div></details>`;}
let testLoadedTicket=null;
$('test-load-ticket').addEventListener('click',async()=>{const id=Number($('test-ticket-id').value);if(!Number.isInteger(id)||id<=0){showError('Informe um número de chamado válido.');return;}try{const ticket=await api(`/api/admin/ticket-context/${id}`);$('test-subject').value=ticket.subject||'';$('test-problem').value=ticket.problem||'';testLoadedTicket=id;$('test-ticket-note').textContent=`#${id} carregado da base local · ${ticket.company||'Empresa não informada'}. Confira o contexto antes de gerar.`;clearError();}catch(err){testLoadedTicket=null;showError(err.message);}});
$('test-ticket-id').addEventListener('input',()=>{testLoadedTicket=null;$('test-ticket-note').textContent='Carregue o chamado da base local antes de gerar a resposta vinculada.';});
$('test-response-form').addEventListener('submit',async e=>{e.preventDefault();clearError();const button=$('test-generate');button.disabled=true;$('test-result').innerHTML='';const subject=$('test-subject').value.trim(),problem=$('test-problem').value.trim(),ticket_id=$('test-ticket-id').value?Number($('test-ticket-id').value):null;if(ticket_id!==null&&testLoadedTicket!==ticket_id){button.disabled=false;showError('Carregue o chamado da base local antes de gerar a resposta vinculada.');return;}try{$('test-progress').textContent='Consultando referências na base…';const search=await api('/api/search',{method:'POST',body:JSON.stringify({subject,problem,top_k:5})});if(!search.results?.length)throw new Error('Nenhuma referência permitida foi encontrada. Acrescente informações ao contexto.');$('test-progress').textContent='Gerando pré-resposta com as referências encontradas…';const result=await api('/api/draft',{method:'POST',body:JSON.stringify({ticket_id,subject,problem,top_results:search.results})});$('test-result').innerHTML=`<article class="panel run-card" style="margin-top:16px"><div class="panel-head"><div><h2>Pré-resposta gerada</h2><p>Confira o procedimento antes de usar no atendimento.</p></div><span class="status-chip">Aguardando revisão</span></div><div class="response-text" style="margin-top:16px">${esc(result.draft)}</div>${decisionHtml(result.decision)}<div class="save-row"><span>Registrada no histórico de respostas.</span><button class="button button-primary" id="test-open-review">Avaliar resposta</button></div></article>`;$('test-progress').textContent='Geração concluída. Registre a avaliação para atualizar os indicadores.';$('test-open-review').addEventListener('click',()=>{$('review-filter').value='';perfState.page=1;document.querySelector('[data-perf="reviews"]').click();loadPerformance();});await loadPerformance();}catch(err){$('test-progress').textContent='A solicitação não foi concluída.';showError(err.message);}finally{button.disabled=false;}});

let evalData={items:[],labeled:0,total:0,module_options:[]},evalIndex=0;
async function loadEvaluations(){try{evalData=await api('/api/admin/evaluations');evalIndex=Math.min(evalIndex,Math.max(0,evalData.items.length-1));renderEvaluation();}catch(e){showError(e.message);}}
function renderEvaluation(){const d=evalData;const rate=d.labeled?((d.primary_correct/d.labeled)*100).toFixed(1)+'%':'Sem casos revisados';$('eval-summary').textContent=`${d.labeled} de ${d.total} casos revisados · acerto do assunto principal: ${rate} (somente revisados).`;$('eval-prev').disabled=evalIndex<=0;$('eval-next').disabled=evalIndex>=d.items.length-1;$('eval-position').textContent=d.items.length?`${evalIndex+1} de ${d.items.length}`:'Sem casos';if(!d.items.length){$('eval-case').innerHTML='<p class="muted">Monte a amostra para começar a revisão.</p>';return;}const x=d.items[evalIndex];$('eval-case').innerHTML=`<form id="eval-label-form" class="review-form"><h3><a href="https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(x.ticket_id)}" target="_blank" rel="noopener noreferrer">#${esc(x.ticket_id)}</a> · ${esc(x.subject)}</h3><p class="eval-problem">${esc(x.problem)}</p><p class="muted">Triagem atual: ${(x.current_route.module_labels||[]).map(esc).join(' · ')}. ${esc(x.current_route.reason)}</p><details><summary>Solução registrada no histórico (conferir antes de usar)</summary><p class="eval-problem">${esc(x.historical_solution||'Sem solução registrada.')}</p></details><fieldset class="eval-modules"><legend>Assuntos corretos para este caso</legend>${d.module_options.map(m=>`<label><input type="checkbox" name="module" value="${esc(m.key)}" ${x.expected_modules.includes(m.key)?'checked':''}>${esc(m.label)}</label>`).join('')}</fieldset><label>Critérios de uma resposta correta<textarea name="answer" rows="4" minlength="15" maxlength="8000" required placeholder="Descreva o procedimento validado, as informações a conferir e quando pedir ajuda ao técnico.">${esc(x.expected_answer||'')}</textarea></label><div class="save-row"><span>${x.labeled_at?'Caso revisado; você pode corrigir os critérios.':'Aguardando revisão humana.'}</span><button class="button button-primary" type="submit">Salvar avaliação</button></div></form>`;}
$('eval-seed').addEventListener('click',async()=>{try{const result=await api('/api/admin/evaluations/seed',{method:'POST'});evalIndex=0;await loadEvaluations();$('eval-summary').textContent+=` ${result.added} casos novos adicionados.`;}catch(e){showError(e.message);}});
$('eval-prev').addEventListener('click',()=>{if(evalIndex>0){evalIndex--;renderEvaluation();}});
$('eval-next').addEventListener('click',()=>{if(evalIndex<evalData.items.length-1){evalIndex++;renderEvaluation();}});
$('eval-case').addEventListener('submit',async e=>{if(e.target.id!=='eval-label-form')return;e.preventDefault();const f=e.target;const expected_modules=[...f.querySelectorAll('input[name="module"]:checked')].map(x=>x.value);const expected_answer=f.elements.answer.value.trim();try{await api(`/api/admin/evaluations/${evalData.items[evalIndex].ticket_id}`,{method:'PUT',body:JSON.stringify({expected_modules,expected_answer})});await loadEvaluations();}catch(err){showError(err.message);}});

async function loadWebhookReceipts(){
  if(!$('webhook-summary'))return;
  try{
    const d=await api('/api/admin/webhooks/receipts');
    const alive=d.worker && Date.now()/1000-d.worker.heartbeat<900;
    const modes={disabled:'desativado',dry_run:'prévia sem publicar',internal_note:'publicação de nota interna'};
    const ceiling={disabled:'bloqueado no servidor',dry_run:'servidor limitado a prévia',internal_note:'servidor permite nota privada'};
    const automatic=d.requested_auto_internal_note?(modes[d.effective_mode]||d.effective_mode):'desativado';
    const manual=d.manual_mode==='internal_note'?'publicação liberada':'indisponível';
    $('webhook-summary').textContent=`${fmt(d.total)} entrega(s). Automático: ${automatic}. Manual: ${manual}. ${ceiling[d.ceiling_mode]||''}. ${fmt(d.selected_organizations||0)} empresa(s) e ${fmt(d.selected_requesters||0)} solicitante(s) autorizado(s). Worker: ${alive?'ativo':'sem atividade recente'}.`;
    const noteToggle=$('setting-auto-internal-note');
    if(noteToggle){noteToggle.checked=!!d.requested_auto_internal_note;noteToggle.disabled=d.ceiling_mode!=='internal_note';noteToggle.title=noteToggle.disabled?'O .env.server precisa usar CORTEX_AUTONOTE_MODE=internal_note.':'';}
    const labels={received:'Na fila',processing:'Em análise',retry:'Aguardando nova tentativa',completed:'Concluído',preview:'Prévia gerada',skipped:'Sem geração necessária',failed:'Falha',needs_review:'Requer revisão'};
    $('webhook-list').innerHTML=d.recent.length?d.recent.map(x=>`<div class="webhook-event"><b>#${esc(x.ticket_id)}</b> <span>${dateTime(x.received_at)}</span> <small>${x.outcome==='reanalise_agendada'?'Mensagem atualizada — nova análise agendada':`${esc(labels[x.state]||x.state)} — ${esc(x.last_error||x.outcome||'')}`}</small></div>`).join(''):'<p class="muted">Nenhum evento recebido.</p>';
    $('webhook-notes').innerHTML=(d.notes||[]).map(n=>`<details><summary>Nota #${esc(n.ticket_id)} · ${esc(n.state)}</summary><pre style="white-space:pre-wrap">${esc(n.text)}</pre></details>`).join('');
    return d;
  }catch(e){$('webhook-summary').textContent='Não foi possível consultar o worker.';showError(e.message);}
}
$('webhook-refresh')?.addEventListener('click',loadWebhookReceipts);
let webhookMonitorBusy = false;
setInterval(async () => {
  if (document.hidden || !$('view-agent')?.classList.contains('active') || webhookMonitorBusy) return;
  webhookMonitorBusy = true;
  try { await loadWebhookReceipts(); await window.refreshGovernance?.(); }
  finally { webhookMonitorBusy = false; }
}, 15000);
$('webhook-pull-form')?.addEventListener('submit',async event=>{
  event.preventDefault();
  const input=$('webhook-ticket-id'),button=$('webhook-pull'),status=$('webhook-pull-status');
  const ticketId=Number(input?.value);
  if(!Number.isInteger(ticketId)||ticketId<=0){status.textContent='Informe um número de chamado válido.';return;}
  if(!confirm(`Processar o chamado #${ticketId}? Esta ação pode publicar uma nota interna privada no Movidesk conforme as autorizações vigentes.`))return;
  button.disabled=true;status.textContent=`Buscando o chamado #${ticketId} no Movidesk…`;clearError();
  try{
    const result=await api(`/api/admin/webhooks/pull/${ticketId}`,{method:'POST'});
    if(result.status==='already_processed'){
      status.textContent=`A última mensagem do chamado #${ticketId} já foi processada.`;
    }else{
      status.textContent=`Ação #${result.action_id} carregada e enviada para processamento.`;
      for(let attempt=0;attempt<15;attempt++){
        await new Promise(resolve=>setTimeout(resolve,3000));
        const snapshot=await loadWebhookReceipts();
        const current=(snapshot?.recent||[]).find(item=>Number(item.ticket_id)===ticketId);
        if(!current)continue;
        if(['completed','preview','skipped','failed','needs_review'].includes(current.state)){
          const outcome=current.last_error||current.outcome||current.state;
          status.textContent=`Chamado #${ticketId}: ${outcome}.`;
          break;
        }
        status.textContent=`Chamado #${ticketId} em processamento…`;
      }
    }
    await loadWebhookReceipts();
  }catch(error){status.textContent=`Não foi possível processar o chamado: ${error.message}`;showError(error.message);}
  finally{button.disabled=false;}
});


/* ==========================================================================
   CORTEX SHELL & GLOBAL CONTROLS
   ========================================================================== */
function timeAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  const diffSec = Math.floor((now - d) / 1000);
  if (diffSec < 60) return 'Agora mesmo';
  if (diffSec < 3600) return `Há ${Math.floor(diffSec / 60)} min`;
  if (diffSec < 86400) return `Há ${Math.floor(diffSec / 3600)} h`;
  return `Há ${Math.floor(diffSec / 86400)} dia(s)`;
}

function updateClockAndGreeting() {
  const now = new Date();
  const days = ['Domingo', 'Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado'];
  const months = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
  const dateStr = `${days[now.getDay()]}, ${String(now.getDate()).padStart(2, '0')} de ${months[now.getMonth()]} de ${now.getFullYear()}`;
  const timeStr = now.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });

  const dateEl = $('overview-clock-date');
  const timeEl = $('overview-clock-time');
  if (dateEl) dateEl.textContent = dateStr;
  if (timeEl) timeEl.textContent = timeStr;

  const hour = now.getHours();
  const greetingPeriod = hour < 12 ? 'Bom dia' : hour < 18 ? 'Boa tarde' : 'Boa noite';
  const greetingEl = $('overview-greeting');
  if (greetingEl) {
    const name = state.currentUser?.name || 'Lucas Souza';
    greetingEl.innerHTML = `${greetingPeriod}, <span id="overview-greeting-name">${esc(name)}</span>`;
  }
}

async function initUserProfile() {
  try {
    const user = await api('/api/me');
    state.currentUser = user;
    const name = user.name || 'Lucas Souza';
    const initials = name.split(' ').map(p => p[0]).slice(0, 2).join('').toUpperCase() || 'LS';
    const roleLabel = user.role === 'super_admin' ? 'Super Admin' : user.role === 'supervisor' ? 'Supervisor' : 'Analista';

    const sideName = $('sidebar-user-name');
    const sideAvatar = $('sidebar-user-avatar');
    const sideRole = $('sidebar-user-role');

    if (sideName) sideName.textContent = name;
    if (sideAvatar) sideAvatar.textContent = initials;
    if (sideRole) sideRole.textContent = roleLabel;

    updateClockAndGreeting();
  } catch (_) {}
}

function initShellControls() {
  // Sidebar Collapse & Expand (Robust Toggle)
  const shell = $('app-shell');
  const collapseToggle = $('sidebar-collapse-toggle');
  const brandToggle = $('sidebar-brand-toggle');

  function toggleSidebar(forceState) {
    if (!shell) return;
    const isCollapsed = typeof forceState === 'boolean' ? forceState : !shell.classList.contains('collapsed');
    shell.classList.toggle('collapsed', isCollapsed);
    localStorage.setItem('cortex-sidebar-collapsed', String(isCollapsed));
  }

  if (shell) {
    if (localStorage.getItem('cortex-sidebar-collapsed') === 'true') {
      shell.classList.add('collapsed');
    }
    collapseToggle?.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSidebar();
    });
    brandToggle?.addEventListener('click', () => {
      if (shell.classList.contains('collapsed')) {
        toggleSidebar(false);
      }
    });
  }

  // Home Button
  $('topbar-home-btn')?.addEventListener('click', () => go('overview'));

  // User Profile Dropdown
  const userDropdown = $('topbar-user-dropdown');
  const userMenu = $('user-profile-menu');
  if (userDropdown && userMenu) {
    userDropdown.addEventListener('click', (e) => {
      e.stopPropagation();
      userMenu.hidden = !userMenu.hidden;
    });
    document.addEventListener('click', () => {
      userMenu.hidden = true;
    });
  }
  $('menu-item-settings')?.addEventListener('click', () => {
    if (userMenu) userMenu.hidden = true;
    go('settings');
  });

  // Global Search Input
  const searchInput = $('global-search-input');
  if (searchInput) {
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const q = searchInput.value.trim();
        if (q) drillTickets({ q });
      }
    });
    // Keyboard shortcut '/' focuses search
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement !== searchInput && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
        e.preventDefault();
        searchInput.focus();
      }
    });
  }

  // Bottom action buttons
  $('overview-view-all-tickets')?.addEventListener('click', () => drillTickets({}));
  $('overview-view-all-audit')?.addEventListener('click', () => go('audit'));

  // Initialize clock and user profile
  updateClockAndGreeting();
  setInterval(updateClockAndGreeting, 30000);
  initUserProfile();

  // Hide module-page-head initially if overview
  const moduleHead = $('module-page-head');
  if (moduleHead) moduleHead.style.display = 'none';
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initShellControls);
} else {
  initShellControls();
}
