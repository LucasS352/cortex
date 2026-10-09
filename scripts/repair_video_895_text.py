"""Repair text encoding in the preserved XML-entry draft; keep its procedure and media."""
import knowledge_curation as curation

STEPS = """1. Abra a tela “201 - Movimentação de Compras” e selecione a empresa que receberá a entrada. Na aba Dados, use “(F7) Abrir XML” para escolher o arquivo da nota de compra. Confira número, série, emissão, fornecedor, CFOP e valor total trazidos para a tela.
![Tela de movimentação de compras após abrir o XML](/curation_media/895/frame-045.jpg)

2. Na aba “Itens da Compra”, confira os produtos importados, a quantidade de entrada e os valores. Relacione cada item do XML ao cadastro correto do Master antes de confirmar.
![Itens importados do XML](/curation_media/895/frame-080.jpg)

3. Quando um item do XML não corresponder diretamente a um produto cadastrado, use a tela “Relacionar Item do XML”. Pesquise o produto do Master, ajuste a quantidade e confirme a relação. No vídeo também aparece a pesquisa por descrição; confira a identificação antes de selecionar.
![Relacionar item do XML](/curation_media/895/frame-120.jpg)
![Pesquisa do produto no Master](/curation_media/895/frame-140.jpg)

4. Revise os itens relacionados, as quantidades de entrada, preços de compra, CFOP e a opção de movimentar estoque conforme o caso. Não confirme dados fiscais ou de estoque apenas porque foram preenchidos automaticamente.
![Itens e quantidades revisados](/curation_media/895/frame-180.jpg)

5. Confira a aba “Condições de Pagamento” e a aba “Impostos”, comparando parcelas, vencimentos, bases, tributos, frete e total com a nota recebida.
![Condições de pagamento](/curation_media/895/frame-200.jpg)
![Impostos e frete](/curation_media/895/frame-220.jpg)

6. Salve a movimentação e confirme a mensagem “Registro Gravado com Sucesso”. Depois, confira a compra gravada e o efeito no estoque conforme a configuração utilizada.
![Confirmação do registro](/curation_media/895/frame-240.jpg)"""


def main():
    curation.ensure_schema()
    with curation.connect() as conn:
        row = conn.execute("SELECT * FROM knowledge_curation WHERE node_id=895 FOR UPDATE").fetchone()
        if not row or row['revision'] != 4:
            raise RuntimeError("Revisão mudou; conferir o artigo antes de corrigir texto.")
        update = {key: row[key] for key in (
            'module_key', 'subtopic_key', 'status', 'title_override', 'keywords', 'image_annotations')}
        update.update({
            'problem_override': 'Entrada de uma nota fiscal de compra recebida em XML na tela de Movimentação de Compras. Rascunho baseado em vídeo oficial antigo; conferir o caminho e os campos na versão atual antes de orientar clientes.',
            'solution_override': STEPS,
            'prerequisites': 'Ter o arquivo XML da nota de compra, identificar a empresa correta e conferir fornecedor e produtos correspondentes no Master. Confirmar permissões e regras fiscais aplicáveis na versão atual.',
            'expected_result': 'Compra gravada com sucesso, com fornecedor, itens, quantidades, valores, impostos, parcelas e movimentação de estoque conferidos em relação ao XML.',
            'version_note': 'Vídeo mostra Master v1.0.004 (2016); validar o procedimento na versão atual antes da aprovação.',
            'change_reason': 'Correção de caracteres corrompidos na codificação do rascunho; conteúdo, quadros e estado de revisão preservados.',
        })
        saved = curation.curate(conn, 895, update, row['revision'])
    print(f"Artigo 895: revisão {saved['revision']} ({saved['status']})")


if __name__ == '__main__':
    main()
