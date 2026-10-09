(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    let rows = [], nextOffset = null, token = 0, detailToken = 0;
    const changes = {first_observation:'Primeiro envio',empresa:'Nome da empresa',version:'Versão',build_local:'Compilação',is_beta:'Estável/BETA',beta_ticket:'Ticket BETA',server:'Servidor',database:'Banco ativo',config_hash:'Configuração'};
    const warnings = {provisional_identity:'Faltam identificadores fixos da empresa/instalação',repeated_ini_sections_preserved:'O INI contém seções repetidas, preservadas separadamente',duplicate_keys_last_value_used:'Há chaves repetidas no mesmo bloco; foi considerado o último valor',active_connection_not_found:'A conexão ativa não foi localizada no INI',multiple_active_connection_matches:'Mais de um bloco corresponde à conexão ativa',unparsed_ini_lines_omitted:'Algumas linhas não puderam ser interpretadas',build_after_observation_check_clocks:'A compilação aparece posterior à coleta; conferir os relógios',device_clock_ahead_not_promoted:'Relógio de coleta adiantado; envio não aplicado ao estado atual'};
    const make = (tag, text, parent) => {const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(parent)parent.append(el); return el;};
    const date = value => value ? new Date(value).toLocaleString('pt-BR') : 'Não informado';
    async function get(url) {
        const res=await fetch(url,{signal:AbortSignal.timeout(15000)}); const body=await res.json();
        if(!res.ok) throw new Error(typeof body.detail==='string'?body.detail:'Consulta indisponível.'); return body;
    }
    function draw() {
        $('rows').replaceChildren();
        const q=$('filter').value.toLocaleLowerCase();
        for(const row of rows.filter(r=>(r.environment?.empresa||'').toLocaleLowerCase().includes(q))) {
            const env=row.environment||{}, tr=make('tr',undefined,$('rows'));
            const company=make('td',env.empresa||'Aguardando horário válido',tr);
            make('small',env.instalacao_id||'Identificação provisória',company);
            const version=make('td',env.version||'—',tr); make('small',env.build_local?'Compilação: '+env.build_local.replace('T',' '):'Compilação não informada',version);
            const channel=make('td',undefined,tr), badge=make('span',env.is_beta?'BETA':'ESTÁVEL',channel); badge.className='badge'+(env.is_beta?' beta':'');
            if(!row.environment) badge.textContent='SEM ESTADO ATUAL';
            if(env.beta_ticket)make('small',env.beta_ticket,channel);
            const server=make('td',env.server||'—',tr); make('small',env.database||'—',server);
            const observed=make('td',date(row.latest_observed_at),tr); if(row.stale)make('small','Envio pendente ou desatualizado',observed).className='stale';
            const cell=make('td',undefined,tr), btn=make('button','Ver mudanças',cell); btn.onclick=()=>history(row);
        }
    }
    async function load(offset=0) {
        const own=++token; $('refresh').disabled=true; $('more').hidden=true; $('status').textContent='Consultando inventário…';
        try {
            const result=await get('/api/v1/master/environments?'+new URLSearchParams({offset:String(offset),beta_only:String($('beta').checked)}));
            if(own!==token)return; rows=result.items; nextOffset=result.next_offset; draw();
            $('status').textContent=rows.length?`${rows.length} ambientes nesta página.`:'Nenhum ambiente recebido ainda. O inventário será preenchido pelos envios diários do Master.';
            $('more').hidden=nextOffset===null;
        }catch(e){if(own===token)$('status').textContent=e.message;}
        finally{if(own===token)$('refresh').disabled=false;}
    }
    async function history(row, offset=0) {
        const own=++detailToken; $('content').replaceChildren(); make('h2',row.environment?.empresa||'Histórico do ambiente',$('content'));
        const loading=make('p','Carregando envios…',$('content')); if(!$('detail').open)$('detail').showModal();
        try {
            const result=await get(`/api/v1/master/environments/${row.id}/history?offset=${offset}`); if(own!==detailToken)return; loading.remove();
            for(const entry of result.items) {
                const env=entry.environment, article=make('article',undefined,$('content'));
                make('strong',`${date(entry.observed_at)} · ${env.version} · ${env.is_beta?'BETA '+env.beta_ticket:'Estável'}`,article);
                make('p','Mudanças registradas: '+(entry.changed_fields.map(key=>changes[key]||key).join(', ')||'Sem alteração de configuração'),article);
                if(!entry.promoted)make('p','Este envio foi preservado no histórico e não substituiu o estado atual.',article);
                make('small',`Recebido em ${date(entry.received_at)} · ${env.connection_count} blocos de conexão`,article);
                const btn=make('button','Ver configuração de diagnóstico',article); btn.onclick=()=>config(row,entry);
            }
            if(result.next_offset!==null){const btn=make('button','Envios anteriores',$('content')); btn.onclick=()=>history(row,result.next_offset);}
        }catch(e){if(own===detailToken)loading.textContent=e.message;}
    }
    async function config(row,entry) {
        const own=++detailToken; $('content').replaceChildren(); const info=make('p','Carregando configuração…',$('content'));
        try {
            const result=await get(`/api/v1/master/environments/${row.id}/snapshots/${entry.id}`); if(own!==detailToken)return;
            info.remove(); const env=result.environment;
            make('h2',`${env.empresa} · Configuração de ${date(result.observed_at)}`,$('content'));
            make('p','A versão pertence ao ambiente ativo informado no envio. As conexões abaixo podem apontar para outros bancos.',$('content'));
            make('p',`${env.sanitization.removed_sensitive_fields} campos sensíveis removidos. ${env.sanitization.duplicate_sections} seções repetidas preservadas.`,$('content'));
            if(env.warnings.length)make('p','Verificações: '+env.warnings.map(key=>warnings[key]||key).join('; '),$('content'));
            for(const section of env.config_sections){const block=make('details',undefined,$('content'));
                make('summary',`[${section.section}] · ocorrência ${section.occurrence}${env.active_connection_occurrences.includes(section.occurrence)?' · corresponde ao ambiente ativo':''}`,block);
                make('pre',Object.entries(section.values).map(([k,v])=>`${k}=${v}`).join('\n'),block);
            }
            const back=make('button','Voltar ao histórico',$('content')); back.onclick=()=>history(row);
        }catch(e){if(own===detailToken)info.textContent=e.message;}
    }
    $('close').onclick=()=>{$('detail').close();}; $('detail').addEventListener('close',()=>{detailToken++;});
    $('filter').addEventListener('input',draw); $('beta').addEventListener('change',()=>load());
    $('refresh').onclick=()=>load(); $('more').onclick=()=>load(nextOffset); load();
})();
