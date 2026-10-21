import os
import json
import re
from pathlib import Path
import docx

INSTRUCTIONS_DIR = Path("Instrucoes_master")
ASSETS_DIR = Path("assets/screens")

ASSETS_DIR.mkdir(parents=True, exist_ok=True)

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text.strip("_")

def process_docx(file_path: Path):
    doc_name = file_path.stem
    relative_source = file_path.relative_to(INSTRUCTIONS_DIR)
    slug = "__".join(slugify(part) for part in relative_source.with_suffix("").parts)
    try:
        doc = docx.Document(file_path)
    except Exception as e:
        return {"file": relative_source.as_posix(), "error": str(e)}

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    image_count = 0
    img_dir = ASSETS_DIR / slug

    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            try:
                img_data = rel.target_part.blob
                ext = Path(rel.target_ref).suffix or ".png"
                img_dir.mkdir(parents=True, exist_ok=True)
                img_path = img_dir / f"img_{image_count:02d}{ext}"
                with open(img_path, "wb") as f_img:
                    f_img.write(img_data)
                image_count += 1
            except Exception:
                pass

    return {
        "title": doc_name,
        "slug": slug,
        "file": relative_source.as_posix(),
        "asset_directory": img_dir.as_posix() if image_count else None,
        "paragraphs_count": len(paragraphs),
        "images_count": image_count
    }

def main():
    docx_files = sorted(
        path for path in INSTRUCTIONS_DIR.rglob("*.docx")
        if not path.name.startswith("~$")
    )
    print(f"Processando {len(docx_files)} manuais...")
    total_imgs = 0
    manifest = []
    for idx, f in enumerate(docx_files, 1):
        res = process_docx(f)
        manifest.append(res)
        imgs = res.get("images_count", 0)
        total_imgs += imgs
        if imgs > 0:
            print(f"[{idx}/{len(docx_files)}] {res['title']}: {res['paragraphs_count']} pars, {imgs} imagens.")
    output_dir = Path("output/master_screens")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "extraction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nFinalizado! Total de {total_imgs} imagens extraídas para assets/screens.")
    print("Manifesto salvo em output/master_screens/extraction_manifest.json")

if __name__ == "__main__":
    main()
