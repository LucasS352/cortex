"""UI QA with fictional telemetry inside an always-rolled-back transaction.

Requires migration 003. Open http://127.0.0.1:8002/ambientes.
Stopping the process closes/rolls back its transaction; no fixture is committed.
"""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import uuid
import uvicorn

import master_telemetry
from master_config import MasterConfigRequest, normalize
from simulator_app import app


if __name__ == '__main__':
    conn = master_telemetry.connection()
    try:
        now = datetime.now(timezone.utc)
        installation = 'qa-' + str(uuid.uuid4())
        for delta, ticket in ((timedelta(days=1), ''), (timedelta(minutes=1), 'Ticket-FICTICIO')):
            moment = now - delta
            data = {'empresa':'EMPRESA FICTÍCIA — TESTE VISUAL', 'usuario':'Operador de teste',
                    'versao':'v3.0', 'ticket':ticket, 'servidor':'192.0.2.10', 'banco':'/teste/exemplo.fdb',
                    'datahora':moment.astimezone(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S'),
                    'instalacao_id':installation, 'codigo_empresa':'empresa-ficticia',
                    'config':'[conexao_1]\nbanco=/teste/exemplo.fdb\nservidor=192.0.2.10\nsenha=segredo-ficticio-omitido'}
            master_telemetry.save_report(conn, normalize(MasterConfigRequest.model_validate(data), now))
        master_telemetry.connection = lambda: nullcontext(conn)
        uvicorn.run(app, host='127.0.0.1', port=8002)
    finally:
        conn.rollback()
        conn.close()
