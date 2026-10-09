"""Cliente do modelo generativo com prazo máximo real e disjuntor.

Motivo: o urllib aplica o timeout por operação de socket, não ao total da
requisição. Com créditos esgotados (HTTP 402) a API levou ~160 s para
responder, travando o servidor. Aqui a chamada roda numa thread com prazo
fixo e, após falhas definitivas (401/402/403), o provedor fica pausado por
alguns minutos para não penalizar cada requisição.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Lock

import os
from config import GEMINI_API_KEY

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEADLINE_SECONDS = 25
_BLOCKING_HTTP = {401: "Chave do modelo inválida.", 402: "Créditos do provedor esgotados.",
                  403: "Acesso ao modelo negado."}
_COOLDOWN = {"definitive": 600, "transient": 60}

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")
_lock = Lock()
_breaker = {"until": 0.0, "reason": ""}


def status() -> dict:
    with _lock:
        paused = time.time() < _breaker["until"]
        return {"available": bool(GEMINI_API_KEY) and not paused, "model": MODEL,
                "reason": _breaker["reason"] if paused else ("" if GEMINI_API_KEY else "Chave do modelo não configurada."),
                "retry_in_seconds": max(0, int(_breaker["until"] - time.time())) if paused else 0}


def _trip(reason: str, kind: str):
    with _lock:
        _breaker["until"] = time.time() + _COOLDOWN[kind]
        _breaker["reason"] = reason


def _late_failure(future):
    """Classify failures that arrive after the caller already gave up."""
    exc = future.exception()
    if isinstance(exc, urllib.error.HTTPError) and exc.code in _BLOCKING_HTTP:
        _trip(_BLOCKING_HTTP[exc.code], "definitive")


def _response_text(data: dict) -> str:
    candidates = data.get('candidates') or []
    if not candidates:
        raise ValueError('Resposta do modelo sem conteúdo.')
    candidate = candidates[0]
    if candidate.get('finishReason') not in (None, 'STOP'):
        raise ValueError('Resposta incompleta do modelo: ' + str(candidate.get('finishReason')))
    parts = candidate.get('content', {}).get('parts', [])
    return '\n'.join(part['text'] for part in parts
                     if isinstance(part.get('text'), str) and not part.get('thought')).strip()


def _call(prompt: str, temperature: float, max_tokens: int, image: dict | None = None) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}"
    parts = [{"text": prompt}]
    generation = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if image:
        parts.append({'inlineData': image})
        generation['responseMimeType'] = 'application/json'
    payload = json.dumps({"contents": [{"parts": parts}], "generationConfig": generation})
    
    # On Windows, curl.exe avoids socket hang issues with IPv6/WinSock
    import subprocess, shutil
    if shutil.which("curl.exe"):
        p = subprocess.run(
            ["curl.exe", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "--data-binary", "@-"],
            input=payload, capture_output=True, text=True, encoding="utf-8", timeout=DEADLINE_SECONDS
        )
        if p.returncode != 0:
            raise RuntimeError(f"curl falhou com código {p.returncode}: {p.stderr}")
        data = json.loads(p.stdout)
        if "error" in data:
            code = data["error"].get("code", 500)
            msg = data["error"].get("message", "Erro na API do Gemini")
            if code in _BLOCKING_HTTP:
                _trip(_BLOCKING_HTTP[code], "definitive")
            raise urllib.error.HTTPError(url, code, msg, None, None)
        return _response_text(data)

    req = urllib.request.Request(url, data=payload.encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=DEADLINE_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _response_text(data)


def generate(prompt: str, temperature: float = 0.2, max_tokens: int = 8192, image: dict | None = None) -> dict:
    """Retorna {ok, text, error, latency_ms}. Nunca bloqueia além do prazo."""
    st = status()
    if not st["available"]:
        return {"ok": False, "text": "", "error": st["reason"], "latency_ms": 0}
    started = time.time()
    future = (_pool.submit(_call, prompt, temperature, max_tokens, image) if image
              else _pool.submit(_call, prompt, temperature, max_tokens))
    future.add_done_callback(_late_failure)
    try:
        text = future.result(timeout=DEADLINE_SECONDS)
        return {"ok": bool(text), "text": text, "error": "" if text else "Resposta vazia do modelo.",
                "latency_ms": int((time.time() - started) * 1000)}
    except FutureTimeout:
        _trip(f"Modelo não respondeu em {DEADLINE_SECONDS}s.", "transient")
        error = f"Modelo não respondeu em {DEADLINE_SECONDS}s."
    except urllib.error.HTTPError as exc:
        reason = _BLOCKING_HTTP.get(exc.code)
        if reason:
            _trip(reason, "definitive")
            error = reason
        else:
            error = f"Provedor retornou HTTP {exc.code}."
            if exc.code in (429, 500, 502, 503, 504):
                _trip(error, "transient")
    except ValueError as exc:
        message = str(exc)
        error = message if message.startswith(('Resposta incompleta do modelo:', 'Resposta do modelo sem conteúdo.')) else 'Resposta inválida do provedor.'
    except Exception as exc:  # rede, DNS, JSON inesperado
        error = f"Falha ao consultar o modelo ({type(exc).__name__})."
    return {"ok": False, "text": "", "error": error, "latency_ms": int((time.time() - started) * 1000)}
