"""Add verified click targets to the existing XML-entry draft without rewriting its text."""
import knowledge_curation as curation

MARKS = [
    {"url": "/curation_media/895/frame-045.jpg", "x": 0.560, "y": 0.313, "label": "Abrir arquivo XML (F7)"},
    {"url": "/curation_media/895/frame-120.jpg", "x": 0.617, "y": 0.503, "label": "Adicionar produto relacionado"},
    {"url": "/curation_media/895/frame-140.jpg", "x": 0.700, "y": 0.776, "label": "Selecionar produto do Master"},
]


def main():
    curation.ensure_schema()
    with curation.connect() as conn:
        row = conn.execute("SELECT * FROM knowledge_curation WHERE node_id=895 FOR UPDATE").fetchone()
        if not row or row['revision'] != 3:
            raise RuntimeError("Revisão mudou; conferir o artigo antes de adicionar marcações.")
        update = {key: row[key] for key in (
            'module_key', 'subtopic_key', 'status', 'title_override', 'problem_override',
            'solution_override', 'prerequisites', 'expected_result', 'version_note', 'keywords')}
        update['image_annotations'] = MARKS
        update['change_reason'] = 'Marcação dos cliques conferida nos quadros do vídeo oficial de entrada por XML.'
        saved = curation.curate(conn, 895, update, row['revision'])
    print(f"Artigo 895: revisão {saved['revision']} ({saved['status']})")


if __name__ == '__main__':
    main()
