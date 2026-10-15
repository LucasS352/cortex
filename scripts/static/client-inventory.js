(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const state={items:[],next:null,loaded:false,loading:false,seq:0};
  const labels={enabled:'Ativo',disabled:'Inativo',unset:'Não configurado',configured:'Configurado',redacted:'Protegido'};
  const date=value=>{if(!value)return 'Sem coleta válida';const d=new Date(value);return Number.isNaN(d.getTime())?'Data inválida':d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'});};
  const make=(tag,text,className)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
  async function get(url){const response=await fetch(url,{signal:AbortSignal.timeout(15000)});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||'Inventário indisponível.');return body;}
  const company=env=>env.empresa_caminho||env.empresa||'Cliente não identificado';
  const connectionName=section=>section.values?.descricao||section.section.replace('_',' ');
  function connections(env){const sections=(env.config_sections||[]).filter(section=>/^conexao_\d+$/i.test(section.section));if(sections.length)return sections;return (env.integration_summary||[]).map(item=>({section:item.section,occurrence:item.occurrence,values:item}));}
  function matches(item,query){if(!query)return true;const env=item.environment||{};const text=[company(env),env.empresa,env.server,env.database,...connections(env).flatMap(s=>[s.values?.descricao,s.values?.sincronizar,s.values?.empresasinc,s.values?.muir_codigo])].join(' ').toLocaleLowerCase();return text.includes(query);}
  function render(){
    const box=$('client-inventory-list'),query=$('client-inventory-search').value.trim().toLocaleLowerCase();box.replaceChildren();
    const visible=state.items.filter(item=>matches(item,query));
    const environments=state.items.filter(x=>x.environment);const totalConnections=environments.reduce((sum,x)=>sum+Number(x.environment.connection_count||0),0);
    const kpis=[['Clientes recebidos',environments.length],['Integrações mapeadas',totalConnections],['Versões BETA',environments.filter(x=>x.environment.is_beta).length],['Coleta pendente',state.items.filter(x=>x.stale).length]];
    $('client-inventory-kpis').replaceChildren(...kpis.map(([label,value])=>{const card=make('article',undefined,'panel client-kpi');card.append(make('span',label),make('b',String(value)));return card;}));
    if(!visible.length){const empty=make('article',undefined,'panel client-empty');empty.append(make('strong',query?'Nenhum cliente corresponde à busca.':'Nenhum cliente recebido ainda.'),make('span',query?'Revise o termo ou limpe o filtro.':'A lista será preenchida assim que o webhook enviar o primeiro config.ini.'));box.append(empty);return;}
    visible.forEach(item=>{
      const env=item.environment||{},card=make('article',undefined,'panel client-card'),head=make('div',undefined,'client-card-head'),identity=make('div',undefined,'client-identity');
      identity.append(make('h2',company(env)),make('p',env.empresa_caminho&&env.empresa&&env.empresa_caminho.toLocaleLowerCase()!==env.empresa.toLocaleLowerCase()?`Informado pelo webhook: ${env.empresa}`:(env.instalacao_id?`Instalação ${env.instalacao_id}`:'Identificação provisória')));
      const meta=make('div',undefined,'client-meta');
      const channel=make('span',env.is_beta?`BETA${env.beta_ticket?' · '+env.beta_ticket:''}`:'Estável','client-pill '+(env.is_beta?'warn':'ok'));
      meta.append(channel,make('span',`${env.connection_count||0} conexões`,'client-pill'),make('span',env.version||'Versão não informada','client-pill'),make('span',date(item.latest_observed_at),'client-pill '+(item.stale?'warn':'')));
      const open=make('button','Ver configurações','button button-quiet');open.type='button';open.dataset.open='';open.onclick=()=>detail(item);
      head.append(identity,meta,open);card.append(head);
      const integrations=make('div',undefined,'client-integrations'),list=connections(env);
      if(list.length)list.slice(0,8).forEach(section=>{const chip=make('span',connectionName(section),'integration-chip');const sync=section.values?.sincronizar,active=section.values?.enabled===true||section.values?.atualizador?.toLocaleLowerCase()==='s';chip.append(make('small',`${active?'● Ativa · ':''}${sync?sync.split(',').join(' · '):`Empresa ${section.values?.empresasinc||'—'}`}`));integrations.append(chip);});
      else integrations.append(make('span','As conexões estarão disponíveis nos detalhes após a coleta.','muted'));
      if(list.length>8)integrations.append(make('span',`+ ${list.length-8} integrações`,'integration-chip'));card.append(integrations);box.append(card);
    });
  }
  async function detail(item){
    const env=item.environment||{};$('client-dialog-title').textContent=company(env);const body=$('client-dialog-body');body.replaceChildren(make('p','Carregando configuração sanitizada…','muted'));$('client-inventory-dialog').showModal();
    try{
      const result=await get(`/api/v1/master/environments/${encodeURIComponent(item.id)}/snapshots/${encodeURIComponent(item.latest_snapshot_id)}`),full=result.environment;body.replaceChildren();
      const intro=make('div',undefined,'panel');intro.append(make('strong',`${full.connection_count||0} conexões · ${full.version||'versão não informada'} · coleta ${date(result.observed_at)}`),make('p',`${full.server||'Servidor não informado'} · ${full.database||'Banco não informado'}`,'muted'));body.append(intro);
      (full.config_sections||[]).forEach(section=>{
        const details=make('details',undefined,'integration-detail');if(/^conexao_/i.test(section.section))details.open=true;
        const active=(full.active_connection_occurrences||[]).includes(section.occurrence);details.append(make('summary',`${/^conexao_/i.test(section.section)?connectionName(section):'['+section.section+']'}${active?' · conexão ativa':''}`));
        const content=make('div',undefined,'integration-detail-body'),values=make('div',undefined,'config-values');
        Object.entries(section.values||{}).forEach(([key,value])=>{const cell=make('div',undefined,'config-value');cell.append(make('b',key),make('span',value||'—'));values.append(cell);});if(values.childElementCount)content.append(values);
        const grid=make('div',undefined,'parameter-grid');Object.entries(section.parameters||{}).sort(([a],[b])=>a.localeCompare(b)).forEach(([key,status])=>{const row=make('div',undefined,'parameter');row.append(make('code',key),make('span',labels[status]||status,'parameter-state '+status));grid.append(row);});
        content.append(grid);details.append(content);body.append(details);
      });
    }catch(error){body.replaceChildren(make('p',error.message,'notice'));}
  }
  async function load(append=false){
    if(state.loading)return;state.loading=true;const seq=++state.seq;$('client-inventory-refresh').disabled=true;$('client-inventory-status').textContent='Consultando inventário recebido pelo webhook…';
    try{const offset=append&&state.next!==null?state.next:0;const data=await get('/api/v1/master/environments?'+new URLSearchParams({offset:String(offset),limit:'50',beta_only:String($('client-inventory-beta').checked)}));if(seq!==state.seq)return;state.items=append?state.items.concat(data.items):data.items;state.next=data.next_offset;state.loaded=true;render();$('client-inventory-status').textContent=state.items.length?`${state.items.length} cliente(s) no inventário atual. Dados sensíveis foram removidos na entrada.`:'Nenhum envio recebido com este filtro.';$('client-inventory-more').hidden=state.next===null;}
    catch(error){if(seq===state.seq)$('client-inventory-status').textContent=error.message;}
    finally{if(seq===state.seq){state.loading=false;$('client-inventory-refresh').disabled=false;}}
  }
  window.loadClientInventory=()=>load(false);
  document.addEventListener('DOMContentLoaded',()=>{
    $('client-inventory-search').addEventListener('input',render);$('client-inventory-beta').addEventListener('change',()=>load(false));$('client-inventory-refresh').addEventListener('click',()=>load(false));$('client-inventory-more').addEventListener('click',()=>load(true));$('client-dialog-close').addEventListener('click',()=>$('client-inventory-dialog').close());
  });
})();
