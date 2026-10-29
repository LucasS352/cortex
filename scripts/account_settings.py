"""Master account administration and self-service password change."""
from __future__ import annotations

from contextlib import contextmanager
from html import escape
from pathlib import Path
import re
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from config import POSTGRES_DSN
import web_auth

router = APIRouter()
STATIC = Path(__file__).parent / 'static'
Role = Literal['super_admin', 'director', 'support_supervisor', 'support_analyst']
ROLES = [
    {'value': 'super_admin', 'label': 'Master', 'description': 'Acesso completo ao painel, configurações e gestão de contas.'},
    {'value': 'director', 'label': 'Diretor', 'description': 'Área pessoal de diretoria. Painel específico ainda em preparação; sem acesso à administração.'},
    {'value': 'support_supervisor', 'label': 'Supervisor de suporte', 'description': 'Área pessoal de supervisão. Painel específico ainda em preparação; sem acesso à administração.'},
    {'value': 'support_analyst', 'label': 'Analista de suporte', 'description': 'Área pessoal de atendimento. Módulo específico ainda em preparação; sem acesso à administração.'},
]
PUBLIC = 'id::text, username, name, email, role, movidesk_person_id, is_active, must_change_password, created_at, updated_at'
AUDIT_FIELDS = ('username', 'name', 'email', 'role', 'movidesk_person_id', 'is_active', 'must_change_password')


def authenticated(request: Request) -> dict:
    user = getattr(request.state, 'cortex_user_obj', None)
    if not user or not getattr(request.state, 'cortex_authenticated', False):
        raise HTTPException(401, 'Autenticação necessária.')
    return user


def master(user: dict = Depends(authenticated)) -> dict:
    if user.get('role') != 'super_admin':
        raise HTTPException(403, 'Somente a conta master pode administrar usuários.')
    return user


@contextmanager
def database():
    try:
        with psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=3,
                             options='-c statement_timeout=5000') as db:
            yield db
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, 'Já existe uma conta com esse usuário ou e-mail.') from None
    except psycopg.Error:
        raise HTTPException(503, 'Não foi possível acessar as contas. Tente novamente.') from None


class AccountFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=160)
    role: Role = 'support_analyst'
    movidesk_person_id: str | None = Field(default=None, max_length=50)
    is_active: bool = True
    must_change_password: bool = True

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError('Informe o nome completo.')
        return value

    @field_validator('email')
    @classmethod
    def clean_email(cls, value):
        value = value.strip().lower() if value else None
        if value and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('E-mail inválido.')
        return value or None

    @field_validator('movidesk_person_id')
    @classmethod
    def clean_person(cls, value):
        return value.strip() or None if value else None


def password_value(secret: SecretStr) -> str:
    value = secret.get_secret_value()
    if not 8 <= len(value) <= 1024 or value.isspace():
        raise HTTPException(422, 'A senha deve conter entre 8 e 1024 caracteres.')
    return value


class AccountCreate(AccountFields):
    username: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    password: SecretStr

    @field_validator('username')
    @classmethod
    def normalize_username(cls, value):
        return value.lower()


class AccountUpdate(AccountFields):
    password: SecretStr | None = None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: SecretStr
    new_password: SecretStr


def audit(db, actor, action, user_id, before, after):
    def safe(value):
        return {key: value.get(key) for key in AUDIT_FIELDS} if value else None
    db.execute('INSERT INTO cortex_user_audit (actor, action, target_user_id, before_state, after_state) VALUES (%s,%s,%s,%s,%s)',
               (actor['username'], action, user_id, Jsonb(safe(before)), Jsonb(safe(after))))


def protect_master(db, actor, target, values):
    if str(target['id']) == str(actor['id']) and (not values.is_active or values.role != 'super_admin'):
        raise HTTPException(409, 'Você não pode desativar sua própria conta master nem remover seu próprio perfil.')
    if target['role'] == 'super_admin' and target['is_active'] and (not values.is_active or values.role != 'super_admin'):
        remaining = db.execute("SELECT count(*) AS n FROM cortex_users WHERE role='super_admin' AND is_active AND id<>%s", (target['id'],)).fetchone()['n']
        if not remaining:
            raise HTTPException(409, 'Mantenha pelo menos uma conta master ativa.')


@router.get('/api/me')
def me(user: dict = Depends(authenticated)):
    return {key: user.get(key) for key in ('id', *AUDIT_FIELDS)}


@router.get('/api/admin/accounts')
def list_accounts(actor: dict = Depends(master)):
    with database() as db:
        users = db.execute(f'SELECT {PUBLIC} FROM cortex_users ORDER BY name, username LIMIT 1000').fetchall()
    return {'users': users, 'roles': ROLES, 'current_user_id': str(actor['id'])}


@router.post('/api/admin/accounts', status_code=201)
def create_account(values: AccountCreate, actor: dict = Depends(master)):
    hashed = web_auth.hash_password(password_value(values.password))
    with database() as db:
        db.execute('SELECT pg_advisory_xact_lock(8700299)')
        if values.email:
            duplicate = db.execute('SELECT id FROM cortex_users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)',
                                   (values.username, values.email)).fetchone()
        else:
            duplicate = db.execute('SELECT id FROM cortex_users WHERE lower(username)=lower(%s)',
                                   (values.username,)).fetchone()
        if duplicate:
            raise HTTPException(409, 'Já existe uma conta com esse usuário ou e-mail.')
        result = db.execute(f'''INSERT INTO cortex_users
            (username,name,email,role,movidesk_person_id,is_active,must_change_password,password_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {PUBLIC}''',
            (values.username, values.name, values.email, values.role, values.movidesk_person_id,
             values.is_active, values.must_change_password, hashed)).fetchone()
        audit(db, actor, 'account_created', result['id'], None, result)
    return result


@router.put('/api/admin/accounts/{user_id}')
def update_account(user_id: UUID, values: AccountUpdate, actor: dict = Depends(master)):
    hashed = web_auth.hash_password(password_value(values.password)) if values.password is not None else None
    if hashed and str(user_id) == str(actor['id']):
        raise HTTPException(409, 'Para alterar sua própria senha, use a opção Alterar minha senha.')
    with database() as db:
        # Serializes changes, including concurrent attempts to remove the last master.
        db.execute('SELECT pg_advisory_xact_lock(8700299)')
        target = db.execute(f'SELECT {PUBLIC} FROM cortex_users WHERE id=%s FOR UPDATE', (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'Conta não encontrada.')
        protect_master(db, actor, target, values)
        if str(user_id) == str(actor['id']) and values.must_change_password != target['must_change_password']:
            raise HTTPException(409, 'Use Alterar minha senha para gerenciar a senha da sua conta.')
        if values.email and db.execute('SELECT id FROM cortex_users WHERE lower(email)=lower(%s) AND id<>%s', (values.email, user_id)).fetchone():
            raise HTTPException(409, 'Já existe uma conta com esse e-mail.')
        revoke = bool(hashed or values.role != target['role'] or values.is_active != target['is_active'])
        result = db.execute(f'''UPDATE cortex_users SET name=%s,email=%s,role=%s,movidesk_person_id=%s,
            is_active=%s,must_change_password=%s,password_hash=COALESCE(%s,password_hash),
            session_version=session_version+%s,updated_at=now() WHERE id=%s RETURNING {PUBLIC}''',
            (values.name, values.email, values.role, values.movidesk_person_id, values.is_active,
             True if hashed else values.must_change_password, hashed, int(revoke), user_id)).fetchone()
        audit(db, actor, 'account_password_reset' if hashed else 'account_updated', user_id, target, result)
    return result


@router.get('/senha', response_class=HTMLResponse)
def password_page(user: dict = Depends(authenticated)):
    return HTMLResponse((STATIC / 'account-password.html').read_text(encoding='utf-8'))


@router.post('/api/auth/change-password')
def change_password(values: PasswordChange, user: dict = Depends(authenticated)):
    current = password_value(values.current_password)
    new = password_value(values.new_password)
    if current == new:
        raise HTTPException(422, 'Escolha uma senha diferente da senha atual.')
    with database() as db:
        target = db.execute('SELECT * FROM cortex_users WHERE id=%s FOR UPDATE', (user['id'],)).fetchone()
        if not target or not web_auth.verify_password(current, target['password_hash']):
            raise HTTPException(400, 'A senha atual está incorreta.')
        result = db.execute('''UPDATE cortex_users SET password_hash=%s,must_change_password=false,
            session_version=session_version+1,updated_at=now() WHERE id=%s RETURNING *''',
            (web_auth.hash_password(new), user['id'])).fetchone()
        audit(db, user, 'password_changed', user['id'], target, result)
    # A fresh login keeps the chosen remember-me policy explicit and revokes old sessions.
    response = JSONResponse({'redirect': '/login', 'message': 'Senha alterada. Entre com sua nova senha.'})
    response.delete_cookie(web_auth.COOKIE, path='/')
    return response


@router.get('/atendimento', response_class=HTMLResponse)
@router.get('/em-breve', response_class=HTMLResponse)
def role_home(user: dict = Depends(authenticated)):
    role = next((r['label'] for r in ROLES if r['value'] == user['role']), 'Usuário')
    page = (STATIC / 'account-home.html').read_text(encoding='utf-8')
    return HTMLResponse(page.replace('<!-- NAME -->', escape(user['name'])).replace('<!-- ROLE -->', escape(role)))


# --------------------------------------------------------------------------
# GERENCIAMENTO DE PINS DE TREINAMENTO E CURADORIA
# --------------------------------------------------------------------------

class PinCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    pin: str = Field(pattern=r'^\d{4}$')


class PinUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    pin: str = Field(pattern=r'^\d{4}$')
    is_active: bool = True


@router.get('/api/admin/pins')
def list_pins(user: dict = Depends(master)):
    with database() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text, name, pin, is_active, created_by,
                       created_at::text, updated_at::text
                FROM cortex_technician_pins
                ORDER BY name ASC
            """)
            rows = cur.fetchall()
            return [dict(r) for r in rows]


@router.post('/api/admin/pins', status_code=201)
def create_pin(payload: PinCreate, user: dict = Depends(master)):
    name = payload.name.strip()
    pin = payload.pin.strip()
    with database() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cortex_technician_pins WHERE pin = %s", (pin,))
            if cur.fetchone():
                raise HTTPException(409, f"O PIN {pin} já está cadastrado para outro técnico.")
            cur.execute("""
                INSERT INTO cortex_technician_pins (name, pin, is_active, created_by)
                VALUES (%s, %s, TRUE, %s)
                RETURNING id::text, name, pin, is_active, created_by, created_at::text, updated_at::text
            """, (name, pin, user['username']))
            row = dict(cur.fetchone())
            conn.commit()
            return row


@router.put('/api/admin/pins/{pin_id}')
def update_pin(pin_id: str, payload: PinUpdate, user: dict = Depends(master)):
    name = payload.name.strip()
    pin = payload.pin.strip()
    with database() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cortex_technician_pins WHERE pin = %s AND id <> %s::uuid", (pin, pin_id))
            if cur.fetchone():
                raise HTTPException(409, f"O PIN {pin} já está em uso.")
            cur.execute("""
                UPDATE cortex_technician_pins
                SET name = %s, pin = %s, is_active = %s, updated_at = NOW()
                WHERE id = %s::uuid
                RETURNING id::text, name, pin, is_active, created_by, created_at::text, updated_at::text
            """, (name, pin, payload.is_active, pin_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "PIN não encontrado.")
            conn.commit()
            return dict(row)


@router.delete('/api/admin/pins/{pin_id}')
def delete_pin(pin_id: str, user: dict = Depends(master)):
    with database() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cortex_technician_pins WHERE id = %s::uuid RETURNING id", (pin_id,))
            if not cur.fetchone():
                raise HTTPException(404, "PIN não encontrado.")
            conn.commit()
            return {"status": "deleted"}

