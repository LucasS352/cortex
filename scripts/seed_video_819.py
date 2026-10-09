"""Reviewed first pass from official KB video #212804 (Master v2.0, 2018).

Run once. The procedure stays a draft until checked in the current Master.
"""
from pathlib import Path
import json

import knowledge_curation as curation

NODE_ID = 819
VIDEO_ID = "rHkvMp3wU0g"
ROOT = Path(__file__).parent / "output" / "curation_media" / str(NODE_ID)

STEPS = """1. No menu lateral do Master, pesquise por “compra” e abra Movimentos → Compras → ENTRADA DE NOTAS. Na listagem, inclua um novo registro pelo botão “+”.
![Acesso à entrada de notas](/curation_media/819/frame-060.jpg)
![Novo registro de compra](/curation_media/819/frame-062.jpg)

2. Na aba Dados da “Movimentação de Compras”, confira a empresa e preencha os dados da nota, especialmente número, série, data de emissão e fornecedor. O vídeo mostra que a data de emissão é obrigatória ao gravar.
![Cabeçalho e fornecedor](/curation_media/819/frame-068.jpg)
![Validação da data de emissão](/curation_media/819/frame-082.jpg)

3. Em “Itens da Compra”, pesquise o produto cadastrado no Master e selecione o item correto. Preencha quantidade de entrada e preço de compra conforme o documento recebido; confira o CFOP e a opção de movimentar estoque antes de confirmar a linha.
![Pesquisa do produto](/curation_media/819/frame-074.jpg)
![Confirmação do item](/curation_media/819/frame-078.jpg)

4. Confira o total da compra e os demais dados exigidos pela operação. Use o botão de confirmação no rodapé para gravar. Se o Master indicar campo obrigatório, complete-o e tente novamente; no vídeo, a gravação fica pendente até o preenchimento da emissão.
![Nota preenchida para conferência](/curation_media/819/frame-090.jpg)
![Gravação dos dados](/curation_media/819/frame-110.jpg)"""

ANNOTATIONS = [
    {"url": "/curation_media/819/frame-060.jpg", "x": 0.130, "y": 0.465, "label": "Abrir Entrada de Notas"},
    {"url": "/curation_media/819/frame-062.jpg", "x": 0.317, "y": 0.817, "label": "Incluir novo registro"},
    {"url": "/curation_media/819/frame-068.jpg", "x": 0.454, "y": 0.198, "label": "Informar data de emissão"},
    {"url": "/curation_media/819/frame-068.jpg", "x": 0.353, "y": 0.278, "label": "Selecionar fornecedor"},
    {"url": "/curation_media/819/frame-074.jpg", "x": 0.641, "y": 0.720, "label": "Selecionar produto encontrado"},
    {"url": "/curation_media/819/frame-078.jpg", "x": 0.789, "y": 0.455, "label": "Confirmar item da compra"},
    {"url": "/curation_media/819/frame-090.jpg", "x": 0.673, "y": 0.816, "label": "Gravar movimentação"},
]


def main():
    curation.ensure_schema()
    for seconds in (60, 62, 68, 74, 78, 82, 90, 110):
        path = ROOT / f"frame-{seconds:03d}.jpg"
        if not path.is_file():
            raise FileNotFoundError(path)
    update = {
        "module_key": "purchases", "subtopic_key": "general", "status": "draft",
        "title_override": "Como dar entrada em nota fiscal de compra sem XML no ERP Master?",
        "problem_override": "Registro manual de uma nota fiscal de compra quando não se utiliza XML. Procedimento extraído do vídeo oficial #212804 e pendente de conferência na versão atual do Master.",
        "prerequisites": "Ter os dados da nota e do fornecedor, produto cadastrado e parâmetros fiscais da operação. Conferir empresa, CFOP e regra de estoque antes de gravar.",
        "solution_override": STEPS,
        "expected_result": "Entrada de compra gravada com os dados da nota, fornecedor, itens, quantidades, valores e movimentação de estoque conferidos. O vídeo não demonstra uma conferência final na listagem; validar este resultado no Master atual.",
        "version_note": "Gravação do Master v2.0 em 2018. Caminhos, campos e regras fiscais precisam ser confirmados na versão vigente antes da aprovação.",
        "keywords": ["entrada de compra sem XML", "digitar nota de compra", "lançamento manual de NF de fornecedor", "movimentação de compras", "entrada de notas"],
        "image_annotations": ANNOTATIONS,
        "change_reason": "Rascunho extraído e conferido quadro a quadro do vídeo oficial #212804, com marcações de cliques; requer validação no Master atual.",
    }
    manifest = {"node_id": NODE_ID, "external_kb_id": 212804,
                "video_id": VIDEO_ID, "video_url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                "duration_seconds": 141, "status": "draft", "source_version": "Master v2.0 (2018)",
                "frame_seconds": [60, 62, 68, 74, 78, 82, 90, 110]}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with curation.connect() as conn:
        old = conn.execute("SELECT revision FROM knowledge_curation WHERE node_id=%s", (NODE_ID,)).fetchone()
        if old:
            raise RuntimeError("Artigo já revisado; use o editor para alterações posteriores.")
        result = curation.curate(conn, NODE_ID, update, 0)
    print(f"Artigo {NODE_ID}: revisão {result['revision']} ({result['status']})")


if __name__ == "__main__":
    main()
