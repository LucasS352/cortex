/* Governance UI reuses the existing controls and grounded playground. */
let governanceData = null;
let governanceStatuses = [];
const govEvents = {
  status_changed: "Entrada em status",
  customer_message: "Nova mensagem do cliente",
};
const govState = {
  received: "Aguardando processamento",
  processing: "Processando",
  retry: "Nova tentativa agendada",
  completed: "Concluído",
  preview: "Prévia sem publicação",
  skipped: "Ignorado por regra",
  failed: "Falha",
  needs_review: "Requer revisão",
};
const govTime = (n) => (n ? new Date(n * 1000).toLocaleString("pt-BR") : "—");
function govLogs(items) {
  return `<div class="gov-table-wrap"><table class="gov-table"><thead><tr><th>Chamado / empresa</th><th>Evento / status</th><th>Recebido</th><th>Resultado / regra</th><th>Tempo / tentativas</th></tr></thead><tbody>${items.map((x) => `<tr><td><button type="button" class="button button-quiet" data-gov-ticket="${x.ticket_id}">#${x.ticket_id}</button><br>${esc(x.company || "Empresa ainda não identificada")}</td><td>${esc(x.event)}<br>${esc(x.status || "—")}</td><td>${govTime(x.received_at)}</td><td><span class="gov-tag ${x.error ? "bad" : x.state === "completed" ? "good" : ""}">${esc(govState[x.state] || x.state)}</span><br>${esc(x.reason)}${x.error ? "<br>Falha registrada; consulte o servidor para detalhes técnicos." : ""}<br><small>Regra: ${esc(x.rule || "Não avaliada")}</small></td><td>${x.duration === null ? "—" : x.duration + " s desde recebimento"}<br>${x.attempts} tentativa(s)</td></tr>`).join("") || '<tr><td colspan="5">Nenhuma entrega registrada neste recorte.</td></tr>'}</tbody></table></div>`;
}
window.refreshGovernance = async function () {
  try {
    governanceData = await api("/api/admin/automation/monitor");
    $("gov-checks").innerHTML = governanceData.checks
      .map(
        (c) =>
          `<span class="gov-check ${c.ok ? "gov-good" : "gov-pending"}" title="${esc(c.ok ? "Confirmado" : "Pendente")}">${c.ok ? "✓" : "○"} ${esc(c.label)}</span>`,
      )
      .join("");
    $("gov-logs").innerHTML = govLogs(governanceData.items);
    $("gov-runtime").textContent =
      `Modo: ${governanceData.mode} · Worker: ${governanceData.worker_alive ? "ativo" : "sem atividade recente"} · Últimas ${governanceData.items.length} entregas`;
    window.renderGovernanceCompanies();
  } catch (e) {
    $("gov-runtime").textContent = e.message;
  }
};
window.renderGovernanceCompanies = function () {
  const filter = $("gov-company-filter")?.value || "";
  const rows = studioState.pilotCompanies.filter(
    (c) => !filter || (filter === "authorized" ? c.selected : !c.selected),
  );
  $("pilot-company-results").innerHTML = rows
      .map((c) => {
        const run = governanceData?.items.find((x) => x.company_id === c.id);
        return `<div class="gov-company-row ${c.selected ? "selected" : ""}"><span><b>${esc(c.name)}</b><small>ID ${esc(c.id)} · ${fmt(c.open_tickets)} chamado(s) aberto(s)${run ? " · Última entrega " + govTime(run.received_at) : ""}</small></span><strong class="${c.selected ? "gov-good" : ""}">${c.selected ? "✓ Autorizada" : "Não autorizada"}</strong><button type="button" class="button button-quiet" data-company-detail="${esc(c.id)}">Detalhes</button><button type="button" class="pilot-company-row button button-quiet" data-company-id="${esc(c.id)}" data-selected="${!!c.selected}">${c.selected ? "Revogar" : "Autorizar"}</button></div>`;
      })
      .join("") || '<p class="muted">Nenhuma empresa encontrada.</p>';
};
function govRulesEditor() {
  const s = window.governanceSettings || {},
    r = s.automation_rules || {};
  $("gov-rule-enabled").checked = !!r.enabled;
  document
    .querySelectorAll("[data-gov-event]")
    .forEach(
      (e) => (e.checked = (r.events || ["customer_message"]).includes(e.value)),
    );
  $("gov-statuses").innerHTML = governanceStatuses
    .map(
      (x) =>
        `<option ${r.statuses?.includes(x.name) ? "selected" : ""}>${esc(x.name)}</option>`,
    )
    .join("");
}
async function govInspect(id) {
  $("gov-inspect-result").textContent = "Consultando histórico local…";
  try {
    const d = await api("/api/admin/automation/monitor?ticket_id=" + id),
      t = d.ticket || {},
      last = d.items[0];
    const stages = {
      context: "Recuperação de contexto",
      authorization: "Autorização",
      rules: "Evento e status",
      generation: "Geração IA",
      validation: "Validação",
      publication: "Envio da nota",
      published: "Publicação confirmada",
    };
    const recorded = new Set(last?.trace.map((x) => x.stage) || []);
    $("gov-inspect-result").innerHTML =
      `<p>${esc(t.snapshot_notice || t.reason || "")}</p><p><b>Elegibilidade de empresa/solicitante/mensagem:</b> ${t.eligible ? "Atende à política de acesso" : esc(t.reason)}</p><p><b>Última avaliação de evento/status:</b> ${esc(last?.reason || "Nenhum evento registrado")}</p><p><b>Publicação da mensagem:</b> ${t.published_message ? "Confirmada no registro" : "Não confirmada"} · ${d.worker_alive ? "Worker ativo" : "Worker sem atividade recente"}</p><div class="gov-timeline">${Object.entries(
        stages,
      )
        .map(
          ([k, v]) =>
            `<span class="${recorded.has(k) ? "gov-good" : ""}">${recorded.has(k) ? "✓ Registrada" : "○ Sem registro"}<br>${v}</span>`,
        )
        .join("")}</div>${govLogs(d.items)}`;
  } catch (e) {
    $("gov-inspect-result").textContent = e.message;
  }
}
document.addEventListener("DOMContentLoaded", async () => {
  const root = $("view-agent");
  const bar = root.querySelector(".studio-tabs-bar");
  const cards = root.querySelectorAll("#tab-sandbox .studio-grid-2col > article");
  bar.querySelector('[data-tab="tab-sandbox"]').textContent = "Políticas operacionais";
  root.querySelector(".studio-title-block h2").textContent = "Agente e políticas";
  root.querySelector(".studio-title-block p").textContent =
    "Empresas autorizadas, notas internas, processamento e limites de uso.";
  cards[0].querySelector("h3").textContent =
    "Empresas autorizadas e políticas de publicação";
  cards[0].querySelector(".studio-card-head p").textContent =
    "Defina quem pode receber notas internas e como o agente deve agir.";
  cards[1].querySelector("h3").textContent = "Webhook e processamento";
  cards[2].querySelector("h3").textContent = "Ritmo & Limites Operacionais";
  const whitelist = $("whitelist-input").closest(".whitelist-manager-block");
  $("tab-playground").prepend(whitelist);
  const company = $("pilot-company-search").closest(".pilot-company-block");
  const requester = $("pilot-requester-input").closest(".whitelist-manager-block");
  const actions = $("setting-auto-internal-note").closest(".integration-actions-block");
  const policyGrid = document.createElement("div");
  policyGrid.className = "gov-policy-grid";
  company.before(policyGrid);
  const policyOptions = document.createElement("div");
  policyOptions.className = "gov-policy-options";
  policyOptions.append(requester, actions);
  policyGrid.append(company, policyOptions);
  $("setting-sandbox-mode")
    .closest(".toggle-setting-row")
    .querySelector("b").textContent = "Governança operacional habilitada";
  $("webhook-pull-status").textContent = "Ação manual com publicação, sujeita às autorizações vigentes.";
  const filter = document.createElement("select");
  filter.id = "gov-company-filter";
  filter.setAttribute("aria-label", "Filtrar autorização");
  filter.innerHTML =
    '<option value="">Todas as empresas</option><option value="authorized">Autorizadas</option><option value="unauthorized">Não autorizadas</option>';
  $("pilot-company-search").parentElement.append(filter);
  filter.onchange = window.renderGovernanceCompanies;
  cards[1].querySelector(".studio-card-body").insertAdjacentHTML("beforeend",
    '<div id="gov-checks" class="gov-inline-checks"></div><p id="gov-runtime" class="section-subnote">Consultando worker…</p><details class="gov-disclosure"><summary>Diagnóstico e histórico completo</summary><div class="gov-disclosure-body"><form id="gov-inspect" class="gov-toolbar"><input id="gov-ticket" type="number" min="1" required placeholder="Número do chamado" aria-label="Número do chamado"><button class="button button-quiet">Verificar elegibilidade</button><button type="button" id="gov-preview" class="button button-quiet">Gerar prévia sem publicar</button></form><div id="gov-inspect-result" role="status"></div><div id="gov-logs"></div></div></details>');
  cards[2].querySelector(".studio-card-body").insertAdjacentHTML("beforeend",
    `<details class="gov-disclosure" id="gov-rules"><summary>Gatilhos e regras avançadas</summary><div class="gov-disclosure-body"><p>As regras adicionais não ampliam a autorização das empresas nem as permissões do servidor.</p><label><input type="checkbox" id="gov-rule-enabled"> Aplicar regras configuráveis</label><div class="gov-rule-fields"><fieldset><legend>Eventos permitidos</legend>${Object.entries(govEvents).map(([k, v]) => `<label><input type="checkbox" data-gov-event value="${k}"> ${v}</label>`).join("")}<p>A abertura é configurada no Movidesk; uma mudança de status exige duas fotografias do chamado.</p></fieldset><fieldset><legend>Status elegíveis</legend><select id="gov-statuses" multiple aria-label="Status elegíveis"></select><p>Sem seleção: todos os status abertos.</p></fieldset></div><button class="button button-primary" id="gov-save-rule">Salvar regra</button><p id="gov-rule-message" role="status"></p></div></details>`);
  $("gov-rules").addEventListener("toggle", () => { if ($("gov-rules").open) govRulesEditor(); });
  $("webhook-refresh").addEventListener("click", window.refreshGovernance);
  $("gov-inspect").onsubmit = (e) => {
    e.preventDefault();
    govInspect(Number($("gov-ticket").value));
  };
  $("gov-preview").onclick = async () => {
    const id = Number($("gov-ticket").value);
    if (!Number.isInteger(id) || id <= 0) return;
    $("gov-preview").disabled = true;
    try {
      const d = await api("/api/admin/automation/preview/" + id, {
        method: "POST",
      });
      $("gov-inspect-result").textContent = "Prévia sem publicação: " + d.reply;
    } catch (e) {
      $("gov-inspect-result").textContent = e.message;
    } finally {
      $("gov-preview").disabled = false;
    }
  };
  $("gov-save-rule").onclick = async () => {
    try {
      const s = window.governanceSettings || {};
      const r = {
        enabled: $("gov-rule-enabled").checked,
        events: [...document.querySelectorAll("[data-gov-event]:checked")].map(
          (e) => e.value,
        ),
        statuses: [...$("gov-statuses").selectedOptions].map((e) => e.value),
        companies: s.automation_rules?.companies || {},
      };
      window.governanceSettings = await api("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify({ automation_rules: r }),
      });
      $("gov-rule-message").textContent =
        "Regra salva. As demais autorizações continuam obrigatórias.";
      window.refreshGovernance();
    } catch (e) {
      $("gov-rule-message").textContent = e.message;
    }
  };
  const dialog = document.createElement("dialog");
  dialog.id = "gov-company-dialog";
  root.append(dialog);
  $("pilot-company-results").addEventListener("click", (e) => {
    const b = e.target.closest("[data-company-detail]");
    if (!b) return;
    const c = studioState.pilotCompanies.find(
        (c) => c.id === b.dataset.companyDetail,
      ),
      s = window.governanceSettings || {},
      r = s.automation_rules || {},
      exception = r.companies?.[c.id] || {};
    dialog.innerHTML = `<h2>${esc(c.name)}</h2><p>ID ${esc(c.id)} · ${c.selected ? "Autorizada" : "Não autorizada"}</p><p>Solicitantes: ${esc((s.pilot_requester_emails || []).join(", ") || "Todos os solicitantes da empresa")}</p><p>Status: ${esc((exception.statuses || r.statuses || []).join(", ") || "Todos os status abertos")}</p><p>Eventos: ${esc((exception.events || r.events || ["customer_message"]).map((x) => govEvents[x]).join(", "))}</p><label><input type="checkbox" id="gov-company-enabled" ${exception.enabled !== false ? "checked" : ""} ${c.selected ? "" : "disabled"}> Habilitar automação desta empresa nas regras configuráveis</label><p>A exceção é aplicada quando as regras configuráveis estão habilitadas. Autorizações e filtros globais são obrigatórios.</p><button class="button button-primary" id="gov-company-save" ${c.selected ? "" : "disabled"}>Salvar exceção</button><button class="button button-quiet" id="gov-company-close">Fechar</button><p id="gov-company-message"></p><h3>Últimas execuções no recorte</h3>${govLogs((governanceData?.items || []).filter((x) => x.company_id === c.id))}<p>Histórico de alterações de configuração disponível em Histórico de alterações.</p>`;
    dialog.showModal();
    $("gov-company-close").onclick = () => dialog.close();
    $("gov-company-save").onclick = async () => {
      try {
        const next = {
          enabled: !!r.enabled,
          events: r.events || ["customer_message"],
          statuses: r.statuses || [],
          companies: {
            ...r.companies,
            [c.id]: { ...exception, enabled: $("gov-company-enabled").checked },
          },
        };
        window.governanceSettings = await api("/api/admin/settings", {
          method: "PUT",
          body: JSON.stringify({ automation_rules: next }),
        });
        $("gov-company-message").textContent = "Exceção salva.";
        window.refreshGovernance();
      } catch (err) {
        $("gov-company-message").textContent = err.message;
      }
    };
  });
  $("pilot-company-results").addEventListener("click", (e) => {
    const button = e.target.closest("[data-company-detail]");
    if (!button) return;
    const id = button.dataset.companyDetail,
      s = window.governanceSettings || {},
      r = s.automation_rules || {};
    const exception = r.companies?.[id] || {},
      company = studioState.pilotCompanies.find((c) => c.id === id);
    const editor = document.createElement("div");
    editor.className = "gov-rule-fields";
    editor.innerHTML = `<fieldset><legend>Eventos desta empresa</legend>${Object.entries(
      govEvents,
    )
      .map(
        ([key, label]) =>
          `<label><input type="checkbox" data-company-event value="${key}" ${(exception.events || r.events || ["customer_message"]).includes(key) ? "checked" : ""}> ${label}</label>`,
      )
      .join(
        "",
      )}</fieldset><fieldset><legend>Status desta empresa</legend><select id="gov-company-statuses" multiple aria-label="Status desta empresa">${governanceStatuses.map((x) => `<option ${(exception.statuses || r.statuses || []).includes(x.name) ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select><p>Sem seleção: todos os status abertos.</p></fieldset>`;
    $("gov-company-save").before(editor);
    $("gov-company-save").onclick = async () => {
      if (!company.selected) return;
      try {
        const next = {
          enabled: !!r.enabled,
          events: r.events || ["customer_message"],
          statuses: r.statuses || [],
          companies: {
            ...r.companies,
            [id]: {
              enabled: $("gov-company-enabled").checked,
              events: [
                ...dialog.querySelectorAll("[data-company-event]:checked"),
              ].map((x) => x.value),
              statuses: [...$("gov-company-statuses").selectedOptions].map(
                (x) => x.value,
              ),
            },
          },
        };
        window.governanceSettings = await api("/api/admin/settings", {
          method: "PUT",
          body: JSON.stringify({ automation_rules: next }),
        });
        $("gov-company-message").textContent =
          "Exceção salva. Autorizações e restrições do servidor permanecem obrigatórias.";
        window.refreshGovernance();
      } catch (err) {
        $("gov-company-message").textContent = err.message;
      }
    };
  });
  try {
    governanceStatuses = await api("/api/admin/statuses");
    govRulesEditor();
  } catch (e) {
    $("gov-rule-message").textContent = "Status indisponíveis: " + e.message;
  }
});
