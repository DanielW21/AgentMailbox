from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_BZIP2, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"


def zip(matter_number: str) -> str:
    """Zip all PDFs from downloads/<matter_number> into downloads/<matter_number>.zip."""
    root = DOWNLOADS_DIR
    folder = (root / matter_number).resolve()
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")

    pdf_files = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"])
    if not pdf_files:
        raise ValueError(f"No PDF files found in: {folder}")

    zip_path = root / f"{matter_number}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with ZipFile(zip_path, mode="w", compression=ZIP_BZIP2, compresslevel=9) as archive:
            for i, pdf_file in enumerate(pdf_files, 1):
                archive.write(pdf_file, arcname=pdf_file.name)
                print(f"   [{i}/{len(pdf_files)}] {pdf_file.name}")
    except Exception as e:
        print(f"BZIP2 compression failed: {e}")

    return str(zip_path)