"""Normalize the Delphi daily JSON envelope without retaining raw INI secrets."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator

REDACTED = "[REMOVIDO]"
SAFE_KEYS = set("""n_bancos tempo_aviso_sinc_uc fa admin orc_tamanho orc_espaco
impressora_nf impressora_orc impressora_boleto impressora_pgto ecf_modelo ecf_porta
tela_conferencia exige_digital impressora_barra tam_pag_orc servidor_master orc_modogr
nf_serie nf_layout orc_impepson nf_serie_nfe ecf_baud ecf_sensorpapel rec_modogr
impressora_guilhotina leitorcodbarras terminal_sat impressora_pgto_modelo
impressora_pgto_porta exibir_etiqueta_venda gaveta tef_porta
descricao banco servidor empresasinc dll atualizador sincronizar direct""".split())
SECRET_KEY = re.compile(r"senha|password|passwd|pwd|secret|token|credential|api.?key|private.?key", re.I)
CONNECTION_SECTION = re.compile(r"conexao_\d+", re.I)


def canonical(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip().casefold()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


def safe_value(value):
    # Values can themselves contain connection strings or URL userinfo.
    if re.search(r"\b(?:password|pwd|senha|token|secret)\s*[=:]", value, re.I) or re.search(r"://[^/\s]+@", value):
        return REDACTED
    return value.strip()[:2048]


def parse_ini(text: str):
    """Preserve duplicate sections; ConfigParser(strict=False) loses environments."""
    sections = []
    section = None
    redacted = ignored = malformed = duplicate_keys = 0
    seen_names = set()
    duplicate_sections = 0
    for line in text.splitlines():
        line = line.strip().lstrip('\ufeff')
        if not line or line.startswith(('#', ';')):
            continue
        header = re.fullmatch(r"\[([A-Za-z0-9_ -]{1,64})\]", line)
        if header:
            name = canonical(header[1])
            allowed = name in ('geral', 'individual', 'safetech') or bool(CONNECTION_SECTION.fullmatch(name))
            section = {'section': name, 'occurrence': len(sections) + 1, 'values': {}} if allowed else None
            if allowed:
                if name in seen_names:
                    duplicate_sections += 1
                seen_names.add(name)
                sections.append(section)
            else:
                ignored += 1
            continue
        if line.startswith('['):
            section = None
            malformed += 1
            continue
        if '=' not in line:
            malformed += 1
            continue
        key, value = line.split('=', 1)
        key = canonical(key)
        if SECRET_KEY.search(key) or key == 'usuario':
            redacted += 1
            continue
        if section is None or key not in SAFE_KEYS:
            ignored += 1
            continue
        if key in section['values']:
            duplicate_keys += 1
        cleaned = safe_value(value)
        if cleaned == REDACTED:
            redacted += 1
        section['values'][key] = cleaned
    return sections, {'removed_sensitive_fields': redacted, 'ignored_fields': ignored,
                      'malformed_lines': malformed, 'duplicate_sections': duplicate_sections, 'duplicate_keys': duplicate_keys}


class MasterConfigRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    empresa: str = Field(min_length=1, max_length=200)
    usuario: str = Field(default='', max_length=200)
    versao: str = Field(min_length=1, max_length=150)
    ticket: str = Field(default='', max_length=100)
    servidor: str = Field(min_length=1, max_length=255)
    banco: str = Field(min_length=1, max_length=1024)
    datahora: str = Field(min_length=1, max_length=40)
    config: str = Field(min_length=1, max_length=262144)
    codigo_empresa: str | None = Field(default=None, min_length=1, max_length=100)
    instalacao_id: str | None = Field(default=None, min_length=1, max_length=100)
    event_id: str | None = Field(default=None, min_length=1, max_length=100)
    timezone_offset: str = Field(default='-03:00', pattern=r'^[+-](?:0\d|1[0-4]):[0-5]\d$')

    @model_validator(mode='before')
    @classmethod
    def unwrap(cls, value: Any):
        if isinstance(value, dict) and 'info' in value:
            if value.get('status') != 'ok' or not isinstance(value['info'], dict):
                raise ValueError('Envelope deve informar status ok e objeto info.')
            if set(value) - {'status', 'info'}:
                raise ValueError('Campos desconhecidos no envelope.')
            return value['info']
        return value

    @field_validator('ticket', mode='before')
    @classmethod
    def empty_ticket(cls, value):
        return '' if value is None else value

    @field_validator('empresa', 'usuario', 'versao', 'ticket', 'servidor', 'banco', 'codigo_empresa', 'instalacao_id', 'event_id')
    @classmethod
    def no_control_or_credentials(cls, value):
        if value is not None and (any(ord(c) < 32 for c in value) or safe_value(value) == REDACTED):
            raise ValueError('Campo contém caracteres de controle ou credenciais não permitidas.')
        return value

    @field_validator('datahora')
    @classmethod
    def valid_time(cls, value):
        try:
            datetime.strptime(value, '%d/%m/%Y %H:%M:%S')
        except ValueError:
            raise ValueError('datahora deve usar DD/MM/AAAA HH:MM:SS.')
        return value

    @field_validator('config')
    @classmethod
    def valid_ini(cls, value):
        if not parse_ini(value)[0]:
            raise ValueError('config deve conter ao menos uma seção INI reconhecida.')
        return value


def normalize(req: MasterConfigRequest, received_at=None):
    received_at = received_at or datetime.now(timezone.utc)
    minutes = int(req.timezone_offset[1:3]) * 60 + int(req.timezone_offset[4:6])
    tz = timezone(timedelta(minutes=minutes if req.timezone_offset[0] == '+' else -minutes))
    observed = datetime.strptime(req.datahora, '%d/%m/%Y %H:%M:%S').replace(tzinfo=tz)
    sections, stats = parse_ini(req.config)
    connections = [s for s in sections if CONNECTION_SECTION.fullmatch(s['section'])]
    active = [s['occurrence'] for s in connections if canonical(s['values'].get('servidor')) == canonical(req.servidor)
              and canonical(s['values'].get('banco')) == canonical(req.banco)]
    version = re.fullmatch(r'\s*(.*?)\s+-\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})\s*', req.versao)
    build = None
    if version:
        try:
            build = datetime.strptime(version[2], '%d/%m/%Y %H:%M:%S').isoformat()
        except ValueError:
            pass
    warnings = []
    if not req.codigo_empresa or not req.instalacao_id:
        warnings.append('provisional_identity')
    if stats['duplicate_sections']:
        warnings.append('repeated_ini_sections_preserved')
    if stats['duplicate_keys']:
        warnings.append('duplicate_keys_last_value_used')
    if not active:
        warnings.append('active_connection_not_found')
    elif len(active) > 1:
        warnings.append('multiple_active_connection_matches')
    if stats['malformed_lines']:
        warnings.append('unparsed_ini_lines_omitted')
    if build and datetime.fromisoformat(build) > observed.replace(tzinfo=None):
        warnings.append('build_after_observation_check_clocks')
    clock_trusted = observed <= received_at + timedelta(minutes=10)
    if not clock_trusted:
        warnings.append('device_clock_ahead_not_promoted')
    # Absence of stable identifiers must not collapse independent companies.
    installation_key = 'id:' + req.instalacao_id if req.instalacao_id else 'provisional:' + digest([canonical(req.empresa), canonical(req.servidor), canonical(req.banco)])
    company_key = 'id:' + req.codigo_empresa if req.codigo_empresa else 'provisional:' + digest(installation_key)
    payload = {
        'empresa': req.empresa, 'usuario': req.usuario, 'codigo_empresa': req.codigo_empresa,
        'instalacao_id': req.instalacao_id, 'identity_status': 'reported_identifiers' if req.codigo_empresa and req.instalacao_id else 'provisional',
        'version': version[1].strip() if version else req.versao,
        'version_reported': req.versao, 'build_local': build,
        'is_beta': bool(req.ticket.strip()), 'beta_ticket': req.ticket.strip() or None,
        'server': req.servidor, 'database': req.banco,
        'observed_at': observed.isoformat(), 'timezone_offset': req.timezone_offset,
        'config_sections': sections, 'config_hash': digest(sections), 'sanitization': stats,
        'connection_count': len(connections), 'active_connection_occurrences': active,
        'warnings': warnings,
    }
    # Only sanitized data enters hashes, storage, logs or model-facing context.
    payload_hash = digest({key: value for key, value in payload.items() if key != 'warnings'})
    return {'installation_key': installation_key, 'company_key': company_key,
            'request_key': req.event_id or digest([observed.isoformat(), payload_hash]),
            'payload_hash': payload_hash, 'payload': payload, 'observed_at': observed,
            'clock_trusted': clock_trusted}


def changed_fields(before, after):
    if not before:
        return ['first_observation']
    fields = ('empresa', 'version', 'build_local', 'is_beta', 'beta_ticket', 'server', 'database', 'config_hash')
    return [field for field in fields if before.get(field) != after.get(field)]
