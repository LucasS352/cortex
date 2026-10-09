"""Review-only draft for official Grade X/Y video #207376 (Master v2.0, 2018)."""
import knowledge_curation as curation

STEPS = """1. No menu Cadastros → Estoque → Produtos, abra “Grade X”. Na tela “Cadastro de Grade Horizontal”, inclua uma opção para a primeira dimensão (o vídeo exemplifica cores).
![Acesso à Grade X e inclusão](/curation_media/1325/frame-075.jpg)

2. No cadastro horizontal, informe o nome da opção. Se necessário para sua grade, confira ordem e cor, e grave. Repita para cada valor dessa dimensão. Os nomes e cores mostrados são apenas exemplos do vídeo.
![Nome, ordem e cor da opção horizontal](/curation_media/1325/frame-100.jpg)
![Opções horizontais cadastradas](/curation_media/1325/frame-150.jpg)

3. Abra “Grade Y” e inclua as opções da segunda dimensão (o vídeo exemplifica tamanhos). Na aba Dados, informe o nome, confira a ordem e grave cada opção.
![Acesso à Grade Y](/curation_media/1325/frame-175.jpg)
![Dados da opção vertical](/curation_media/1325/frame-200.jpg)

4. Confira as opções cadastradas nas listagens de Grade X e Grade Y antes de associá-las a um produto. O vídeo mostra o cadastro das opções, mas não demonstra a associação completa ao produto nem a geração das combinações; documentar esse trecho separadamente após validação no Master atual.
![Listagem da Grade Y](/curation_media/1325/frame-210.jpg)"""

MARKS = [
    {'url': '/curation_media/1325/frame-075.jpg', 'x': 0.458, 'y': 0.696, 'label': 'Incluir opção de Grade X'},
    {'url': '/curation_media/1325/frame-100.jpg', 'x': 0.404, 'y': 0.386, 'label': 'Nome da opção horizontal'},
    {'url': '/curation_media/1325/frame-100.jpg', 'x': 0.601, 'y': 0.697, 'label': 'Gravar opção'},
    {'url': '/curation_media/1325/frame-175.jpg', 'x': 0.471, 'y': 0.635, 'label': 'Incluir opção de Grade Y'},
    {'url': '/curation_media/1325/frame-200.jpg', 'x': 0.427, 'y': 0.425, 'label': 'Nome da opção vertical'},
    {'url': '/curation_media/1325/frame-200.jpg', 'x': 0.574, 'y': 0.632, 'label': 'Gravar opção'},
]


def main():
    curation.ensure_schema()
    update = {
        'module_key': 'products', 'subtopic_key': 'grades', 'status': 'draft',
        'title_override': 'Como cadastrar variação e subvariação (Grades X e Y) no ERP Master?',
        'problem_override': 'Cadastro dos valores das duas dimensões de uma grade de produto, como cor e tamanho. O vídeo não mostra a associação final das opções a um produto.',
        'prerequisites': 'Definir previamente quais dimensões e valores a empresa utilizará. Conferir no Master atual as permissões e o caminho dos cadastros.',
        'solution_override': STEPS,
        'expected_result': 'Opções horizontais e verticais cadastradas e visíveis nas respectivas listagens. A formação da grade do produto ainda exige validação e documentação complementar.',
        'version_note': 'Vídeo mostra Master v2.0 em 2018; conferir telas e funcionamento na versão atual antes de aprovar.',
        'keywords': ['grade de produto', 'grade x', 'grade y', 'variação', 'subvariação', 'cor', 'tamanho'],
        'image_annotations': MARKS,
        'change_reason': 'Rascunho conferido no vídeo oficial #207376; limita-se ao cadastro das opções X/Y e aguarda validação atual.',
    }
    with curation.connect() as conn:
        old = conn.execute('SELECT revision FROM knowledge_curation WHERE node_id=1325', ()).fetchone()
        if old:
            raise RuntimeError('Artigo já revisado; use o editor para alterações posteriores.')
        saved = curation.curate(conn, 1325, update, 0)
    print(f"Artigo 1325: revisão {saved['revision']} ({saved['status']})")


if __name__ == '__main__':
    main()
