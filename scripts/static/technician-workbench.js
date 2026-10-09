(() => {
  const $ = id => document.getElementById(id);
  let selectedCompany = null, profileCompany = null, activeConversation = null, profileVersion = 0;

  async function api(path, options={}) {
    const response = await fetch('/api/workbench' + path, {
      ...options, headers: {'Content-Type':'application/json', ...(options.headers||{})}
    });
    let body;
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) throw new Error(body.detail || body.error || `Erro HTTP ${response.status}`);
    return body;
  }
  function status(message, kind='') {
    const box=$('global-status'); box.hidden=!message; box.textContent=message;
    box.className='notice ' + kind;
  }
  function element(tag, text='', className='') {
    const e=document.createElement(tag); if (text) e.textContent=text; if(className)e.className=className; return e;
  }
  function debounce(fn, delay=280) {
    let timer; return (...args) => { clearTimeout(timer); timer=setTimeout(()=>fn(...args),delay); };
  }
  async function lookupCompanies(inputId, optionsId, callback) {
    const q=$(inputId).value.trim(), box=$(optionsId); box.replaceChildren();
    if(q.length<2)return;
    try {
      const data=await api('/companies?q='+encodeURIComponent(q));
      if($(inputId).value.trim()!==q)return;
      if(!data.items.length)box.append(element('p','Nenhuma empresa encontrada.','hint'));
      data.items.forEach(item=>{
        const b=element('button',item.name);b.type='button';
        b.append(element('small','ID Movidesk: '+item.id));
        b.addEventListener('click',()=>{box.replaceChildren();$(inputId).value=item.name;$(inputId).dataset.selectedId=item.id;callback(item);});
        box.append(b);
      });
    } catch(e){status(e.message,'error');}
  }
  function companyPicker(input,options,callback,onClear){
    $(input).addEventListener('input',()=>{
      delete $(input).dataset.selectedId;
      if(onClear)onClear();
      debounceLookup();
    });
    const debounceLookup=debounce(()=>lookupCompanies(input,options,callback));
  }
  async function setCompany(item) {
    selectedCompany=item;
    $('company-search').value=item.name;
    $('company-search').dataset.selectedId=item.id;
    $('selected-company').textContent=`${item.name} · ID ${item.id}`;
    $('company-context-link').hidden=false;
    try {
      const profile=await api('/companies/'+encodeURIComponent(item.id));
      const box=$('company-context-summary');box.replaceChildren();
      box.append(element('strong','Contexto confirmado da empresa'));
      const confirmed=profile.facts.filter(f=>f.verified);
      if(confirmed.length){const ul=element('ul');confirmed.slice(0,5).forEach(f=>ul.append(element('li',f.text)));box.append(ul);}
      else box.append(element('p','Ainda não há particularidades confirmadas.','hint'));
      if(profile.environments.length)box.append(element('p',`${profile.environments.length} ambiente(s) Master vinculado(s).`,'hint'));
      box.hidden=false;
    } catch(e){status(e.message,'error');}
  }
  async function loadRecent(){
    try{const data=await api('/conversations');const box=$('recent-conversations');box.replaceChildren();
      if(!data.items.length)box.append(element('p','Nenhum atendimento registrado.','hint'));
      data.items.forEach(item=>{const b=element('button',item.subject);b.type='button';
        b.append(element('small',`${item.company_name} · ${item.contact_name} · ${item.status}`));
        b.addEventListener('click',()=>openConversation(item.id));box.append(b);});
    }catch(e){status(e.message,'error');}
  }
  function renderMessage(msg){
    const bubble=element('article','','bubble '+msg.role);
    bubble.append(element('strong',msg.role==='assistant'?'Cortex · sugestão interna':'Técnico'));
    bubble.append(element('div',msg.content));
    const sources=msg.sources||[];
    if(sources.length){const box=element('div','','sources');box.append(element('strong','Fontes encontradas (confira a aplicação):'));
      sources.forEach(s=>{const a=element('a',`${s.title || 'Fonte'} · ${s.answer_use==='investigation_only'?'investigar':'referência'}`);
        a.href=s.ticket_id?`https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(s.ticket_id)}`:`/api/knowledge/${encodeURIComponent(s.node_id)}`;
        a.target='_blank';a.rel='noopener noreferrer';box.append(a);});bubble.append(box);}
    $('messages').append(bubble);$('messages').scrollTop=$('messages').scrollHeight;
  }
  function renderPreparation(prep){
    const box=$('case-preparation');box.replaceChildren();
    if(!prep){box.hidden=true;return;}
    box.append(element('strong','Contexto preparado para o atendimento'));
    box.append(element('p',`Empresa: ${prep.company_name}. A IA está pronta para analisar o relato, usando as fontes aplicáveis.`));
    if(prep.verified_facts?.length){
      const facts=element('ul');prep.verified_facts.forEach(f=>facts.append(element('li',f)));
      box.append(element('small','Particularidades confirmadas:'));box.append(facts);
    }
    if(prep.environment_count)box.append(element('small',`${prep.environment_count} ambiente(s) Master vinculado(s) após conferência.`));
    if(prep.related_cases?.length){
      box.append(element('small','Histórico semelhante desta empresa (pista para investigar):'));
      const cases=element('div','','case-links');
      prep.related_cases.forEach(item=>{const a=element('a',`#${item.ticket_id} · ${item.subject}`);
        a.href=`https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(item.ticket_id)}`;
        a.target='_blank';a.rel='noopener noreferrer';cases.append(a);});box.append(cases);
    }
    box.hidden=false;
  }
  async function openConversation(id){
    try{const data=await api('/conversations/'+encodeURIComponent(id));activeConversation=data.conversation;
      $('conversation-title').textContent=activeConversation.subject;
      $('conversation-company').textContent=`${activeConversation.contact_name} · Empresa Movidesk ${activeConversation.movidesk_company_id} · ${activeConversation.status}`;
      $('messages').replaceChildren();data.messages.forEach(renderMessage);
      renderPreparation(data.preparation);
      $('send-message').disabled=false;
      $('confirmed-action').value=activeConversation.confirmed_action||'';
      $('confirmed-result').value=activeConversation.confirmed_result||'';
      $('resolved').checked=activeConversation.status==='resolved';
      const latestDraft=data.drafts?.[0];
      $('ticket-draft').hidden=!latestDraft;
      if(latestDraft){$('draft-subject').value=latestDraft.subject;$('draft-body').value=latestDraft.body;
        $('ticket-draft').querySelector('h3').textContent=`Rascunho ${latestDraft.kind} para conferir`;}
      if(!selectedCompany||selectedCompany.id!==activeConversation.movidesk_company_id){
        const profile=await api('/companies/'+encodeURIComponent(activeConversation.movidesk_company_id));
        await setCompany({id:profile.company_id,name:profile.company_name});
      }
    }catch(e){status(e.message,'error');}
  }
  async function startConversation(){
    if(!selectedCompany||$('company-search').dataset.selectedId!==selectedCompany.id)return status('Selecione uma empresa da lista.','error');
    const contact=$('contact-name').value.trim(),subject=$('subject').value.trim();
    if(contact.length<2||subject.length<5)return status('Informe o contato e o assunto do atendimento.','error');
    try{const data=await api('/conversations',{method:'POST',body:JSON.stringify({company_id:selectedCompany.id,contact_name:contact,subject})});
      status('Atendimento iniciado. Descreva o que o cliente relatou.','success');await openConversation(data.id);await loadRecent();
    }catch(e){status(e.message,'error');}
  }
  async function sendMessage(event){
    event.preventDefault();if(!activeConversation)return;
    const content=$('message-input').value.trim();if(!content)return;
    const button=$('send-message');if(button.disabled)return;button.disabled=true;button.textContent='Investigando…';
    $('message-input').value='';renderMessage({role:'technician',content});
    try{const result=await api('/conversations/'+activeConversation.id+'/messages',{method:'POST',body:JSON.stringify({content})});
      renderMessage({role:'assistant',content:result.reply,sources:result.sources});
      if(result.model&&result.model.error)status('Modelo indisponível; a orientação exibida veio da fonte encontrada. '+result.model.error,'error');else status('Sugestão gerada. Confira a fonte e valide no ambiente do cliente.','success');
      await loadRecent();
    }catch(e){status(e.message,'error');await openConversation(activeConversation.id);}
    finally{button.disabled=false;button.textContent='Investigar com IA';}
  }
  async function analyzeImage(file){
    if(!file||!['image/png','image/jpeg','image/webp'].includes(file.type))return status('Use um print PNG, JPEG ou WebP.','error');
    if(file.size>4*1024*1024)return status('Recorte o print para até 4 MB.','error');
    $('image-status').textContent='Lendo o print…';
    try{
      const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});
      const response=await fetch('/api/admin/ticket-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data_url:dataUrl})});
      const result=await response.json();
      if(!response.ok)throw new Error(result.detail||`Erro HTTP ${response.status}`);
      if(!result.text){$('image-status').textContent=result.notice;return;}
      const input=$('message-input');
      input.value=[input.value.trim(),`[Mensagem lida do print — confira antes de enviar]\n${result.text}`].filter(Boolean).join('\n\n');
      $('image-status').textContent=result.notice;
      input.focus();
    }catch(e){$('image-status').textContent='Não foi possível ler o print.';status(e.message,'error');}
    finally{$('message-image').value='';}
  }
  async function saveOutcome(){
    if(!activeConversation)return status('Selecione um atendimento.','error');
    const action=$('confirmed-action').value.trim(),result=$('confirmed-result').value.trim();
    if(action.length<8||result.length<8)return status('Descreva a ação executada e o resultado observado.','error');
    try{await api(`/conversations/${activeConversation.id}/outcome`,{method:'PUT',body:JSON.stringify({action,result,resolved:$('resolved').checked})});
      status('Ação e resultado registrados como confirmação do técnico.','success');await loadRecent();
    }catch(e){status(e.message,'error');}
  }
  async function makeDraft(kind){
    if(!activeConversation)return status('Selecione um atendimento.','error');
    try{const data=await api(`/conversations/${activeConversation.id}/draft`,{method:'POST',body:JSON.stringify({kind})});
      $('draft-subject').value=data.subject;$('draft-body').value=data.body;$('ticket-draft').hidden=false;
      $('ticket-draft').querySelector('h3').textContent=`Rascunho ${kind} para conferir`;
      $('ticket-draft').scrollIntoView({behavior:'smooth',block:'nearest'});
      status(`Rascunho ${kind} preparado para revisão. Ainda não foi enviado ao Movidesk.`,'success');
    }catch(e){status(e.message,'error');}
  }
  function factRow(f={category:'outro',text:'',verified:false,source_ticket:null}){
    const row=element('div','','fact');
    const cat=element('label','Categoria'),select=element('select');
    ['processo','integracao','fiscal','impressao','ambiente','preferencia','outro'].forEach(v=>{const opt=element('option',v);opt.value=v;select.append(opt)});
    select.value=f.category;cat.append(select);
    const factLabel=element('label','Informação'),input=element('input');input.maxLength=1200;input.value=f.text;input.placeholder='Fato específico da empresa';factLabel.append(input);
    const verified=element('label','Confirmada','check'),check=element('input');check.type='checkbox';check.checked=!!f.verified;verified.prepend(check);
    const source=element('label','Chamado'),ticket=element('input');ticket.type='number';ticket.min=1;ticket.value=f.source_ticket||'';source.append(ticket);
    const remove=element('button','×');remove.type='button';remove.title='Remover informação';remove.addEventListener('click',()=>row.remove());
    row.append(cat,factLabel,verified,source,remove);$('facts-list').append(row);
  }
  function collectFacts(){return [...$('facts-list').querySelectorAll('.fact')].map(row=>({
    category:row.querySelector('select').value,text:row.querySelector('input:not([type=checkbox]):not([type=number])').value.trim(),
    verified:row.querySelector('input[type=checkbox]').checked,
    source_ticket:Number(row.querySelector('input[type=number]').value)||null
  })).filter(f=>f.text);}
  async function loadProfile(item){
    profileCompany=item;
    $('profile-selected-company').textContent=`${item.name} · ID ${item.id}`;
    try{const data=await api('/companies/'+encodeURIComponent(item.id));profileVersion=data.version;
      $('profile-title').textContent=data.company_name;
      $('facts-list').replaceChildren();data.facts.forEach(factRow);
      $('profile-content').hidden=false;
      const historyBox=$('company-history');historyBox.replaceChildren();
      try{const historyData=await api('/companies/'+encodeURIComponent(item.id)+'/history');
        if(!historyData.items.length)historyBox.append(element('p','Nenhum histórico resolvido encontrado.','hint'));
        historyData.items.forEach(ticket=>{const card=element('div','','environment');
          const link=element('a',`#${ticket.ticket_id} · ${ticket.subject}`);
          link.href=`https://helpdesk.alternativasistemas.com.br/Ticket/Edit/${encodeURIComponent(ticket.ticket_id)}`;
          link.target='_blank';link.rel='noopener noreferrer';card.append(link);
          card.append(element('small',`${ticket.quality_tier} · evidência ${ticket.evidence_level||'não classificada'}`));
          historyBox.append(card);});
      }catch(e){historyBox.append(element('p','Não foi possível carregar o histórico.','hint'));}
      const box=$('linked-environments');box.replaceChildren();
      if(!data.environments.length)box.append(element('p','Nenhum ambiente vinculado.','hint'));
      data.environments.forEach(env=>{const card=element('div','','environment');
        const remove=element('button','Desvincular');remove.type='button';remove.addEventListener('click',async()=>{
          try{await api(`/companies/${encodeURIComponent(item.id)}/environments/${env.id}`,{method:'DELETE'});await loadProfile(item);status('Vínculo removido.','success');}
          catch(e){status(e.message,'error');}
        });card.append(remove,element('strong',env.company_reported||'Ambiente Master'));
        card.append(element('small',`${env.version||'Versão não informada'} · ${env.is_beta?'BETA':'Estável'} · coleta ${env.observed_at||'não registrada'}`));
        if(env.active_connections?.length)card.append(element('small','Conexão ativa: '+env.active_connections.map(c=>c.descricao||'sem descrição').join(', ')));
        box.append(card);});
    }catch(e){status(e.message,'error');}
  }
  async function saveProfile(){
    if(!profileCompany)return;
    try{const data=await api('/companies/'+encodeURIComponent(profileCompany.id),{method:'PUT',body:JSON.stringify({version:profileVersion,facts:collectFacts()})});
      profileVersion=data.version;status('Contexto salvo com versão '+profileVersion+'.','success');
    }catch(e){status(e.message,'error');}
  }
  async function findEnvironments(){
    const q=$('environment-search').value.trim(),box=$('environment-options');box.replaceChildren();
    if(q.length<2||!profileCompany)return;
    try{const data=await api('/environments?q='+encodeURIComponent(q));
      if($('environment-search').value.trim()!==q)return;
      data.items.forEach(item=>{const b=element('button',item.name||'Sem nome');b.type='button';
        b.append(element('small',`${item.version||''}${item.linked_company_id?' · vínculo: '+item.linked_company_id:''}`));
        b.disabled=!!item.linked_company_id;
        b.addEventListener('click',async()=>{try{await api(`/companies/${encodeURIComponent(profileCompany.id)}/environments`,{method:'POST',body:JSON.stringify({environment_id:item.id})});
          box.replaceChildren();$('environment-search').value='';await loadProfile(profileCompany);status('Ambiente vinculado. Confira se é a instalação ativa correta.','success');
        }catch(e){status(e.message,'error');}});box.append(b);});
      if(!data.items.length)box.append(element('p','Nenhum ambiente encontrado.','hint'));
    }catch(e){status(e.message,'error');}
  }
  function init(){
    if(!$('view-workbench'))return;
    function showTab(tab){
      const company=tab==='company';
      $('assistant-view').hidden=company;$('company-view').hidden=!company;
      $('wb-tab-assistant').classList.toggle('active',!company);
      $('wb-tab-company').classList.toggle('active',company);
      $('wb-tab-assistant').setAttribute('aria-selected',String(!company));
      $('wb-tab-company').setAttribute('aria-selected',String(company));
      $('wb-tab-assistant').tabIndex=company?-1:0;
      $('wb-tab-company').tabIndex=company?0:-1;
      status('');
    }
    $('wb-tab-assistant').addEventListener('click',()=>showTab('assistant'));
    $('wb-tab-company').addEventListener('click',()=>showTab('company'));
    $('company-context-link').addEventListener('click',()=>{
      showTab('company');
      if(selectedCompany)loadProfile(selectedCompany);
    });
    companyPicker('profile-company-search','profile-company-options',loadProfile,()=>{
      profileCompany=null;$('profile-content').hidden=true;
      $('profile-selected-company').textContent='Selecione uma empresa do cadastro.';
    });
    $('add-fact').addEventListener('click',()=>factRow());
    $('save-profile').addEventListener('click',saveProfile);
    $('environment-search').addEventListener('input',debounce(findEnvironments));
    companyPicker('company-search','company-options',setCompany,()=>{
      selectedCompany=null;
      $('selected-company').textContent='Selecione uma empresa do cadastro.';
      $('company-context-link').hidden=true;$('company-context-summary').hidden=true;
    });
    $('start-conversation').addEventListener('click',startConversation);
    $('message-form').addEventListener('submit',sendMessage);
    $('message-input').addEventListener('keydown',event=>{
      if(event.key!=='Enter'||event.shiftKey||event.isComposing)return;
      event.preventDefault();
      if(!activeConversation)return status('Selecione a empresa, informe o cliente e inicie o atendimento antes de enviar.','error');
      if($('message-input').value.trim()&&!$('send-message').disabled)$('message-form').requestSubmit();
    });
    $('message-image').addEventListener('change',e=>analyzeImage(e.target.files[0]));
    $('message-input').addEventListener('paste',e=>{const file=[...e.clipboardData.items].find(i=>i.type.startsWith('image/'))?.getAsFile();
      if(file){e.preventDefault();analyzeImage(file);}});
    $('save-outcome').addEventListener('click',saveOutcome);
    $('draft-sup').addEventListener('click',()=>makeDraft('SUP'));
    $('draft-dev').addEventListener('click',()=>makeDraft('DEV'));
    $('copy-draft').addEventListener('click',()=>navigator.clipboard.writeText(`${$('draft-subject').value}\n\n${$('draft-body').value}`).then(()=>status('Rascunho copiado.','success')).catch(()=>status('Não foi possível copiar automaticamente.','error')));
    showTab('assistant');
    loadRecent();
  }
  document.addEventListener('DOMContentLoaded',init);
})();
