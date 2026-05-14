from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}


def text_from(node: ET.Element) -> str:
    chunks: list[str] = []
    for text in node.findall(".//w:t", NS):
        if text.text:
            chunks.append(text.text)
    return "".join(chunks)


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def paragraph_style(p: ET.Element) -> str:
    p_style = p.find("./w:pPr/w:pStyle", NS)
    return p_style.attrib.get(f"{{{NS['w']}}}val", "") if p_style is not None else ""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_rels(xml_bytes: bytes) -> dict[str, dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    rels = {}
    for rel in root:
        rel_id = rel.attrib.get("Id")
        if not rel_id:
            continue
        rels[rel_id] = {
            "type": rel.attrib.get("Type", ""),
            "target": rel.attrib.get("Target", ""),
            "mode": rel.attrib.get("TargetMode", ""),
        }
    return rels


def main() -> None:
    base = Path(__file__).resolve().parent
    docx = base / "IEEE_SCADA_Paper_Final.docx"
    out = base / "extracted"
    images_dir = out / "images"
    out.mkdir(exist_ok=True)
    images_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(docx) as zf:
        document_xml = zf.read("word/document.xml")
        rels = parse_rels(zf.read("word/_rels/document.xml.rels"))
        root = ET.fromstring(document_xml)

        paragraphs = []
        body_paras = root.findall(".//w:body/w:p", NS)
        for i, p in enumerate(body_paras, start=1):
            text = normalize_ws(text_from(p))
            if text:
                paragraphs.append(
                    {
                        "index": i,
                        "style": paragraph_style(p),
                        "text": text,
                    }
                )

        tables = []
        for ti, tbl in enumerate(root.findall(".//w:tbl", NS), start=1):
            rows = []
            for tr in tbl.findall("./w:tr", NS):
                cells = [normalize_ws(text_from(tc)) for tc in tr.findall("./w:tc", NS)]
                rows.append(cells)
            tables.append({"index": ti, "rows": rows})

        drawing_records = []
        for di, drawing in enumerate(root.findall(".//w:drawing", NS), start=1):
            record = {"index": di, "name": "", "description": "", "rel_id": "", "target": ""}
            doc_pr = drawing.find(".//wp:docPr", NS)
            if doc_pr is not None:
                record["name"] = doc_pr.attrib.get("name", "")
                record["description"] = doc_pr.attrib.get("descr", "")
            blip = drawing.find(".//a:blip", NS)
            if blip is not None:
                rel_id = blip.attrib.get(f"{{{NS['r']}}}embed", "")
                record["rel_id"] = rel_id
                record["target"] = rels.get(rel_id, {}).get("target", "")
            drawing_records.append(record)

        extracted_images = []
        for name in zf.namelist():
            if not name.startswith("word/media/"):
                continue
            data = zf.read(name)
            target = images_dir / Path(name).name
            target.write_bytes(data)
            extracted_images.append(
                {
                    "path": str(target),
                    "docx_path": name,
                    "bytes": len(data),
                    "sha256": sha256(data),
                }
            )

        props = {}
        if "docProps/core.xml" in zf.namelist():
            core = ET.fromstring(zf.read("docProps/core.xml"))
            for child in core:
                tag = child.tag.split("}", 1)[-1]
                props[tag] = child.text or ""

        content_types = zf.read("[Content_Types].xml").decode("utf-8", errors="replace")

    text_lines = []
    for p in paragraphs:
        prefix = f"[{p['index']:03d}]"
        style = f" ({p['style']})" if p["style"] else ""
        text_lines.append(f"{prefix}{style} {p['text']}")

    (out / "paragraphs.txt").write_text("\n".join(text_lines), encoding="utf-8")
    (out / "tables.json").write_text(json.dumps(tables, indent=2), encoding="utf-8")
    (out / "drawings.json").write_text(json.dumps(drawing_records, indent=2), encoding="utf-8")
    (out / "images.json").write_text(json.dumps(extracted_images, indent=2), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps(props, indent=2), encoding="utf-8")
    (out / "content_types.txt").write_text(content_types, encoding="utf-8")

    print(
        json.dumps(
            {
                "paragraph_count": len(paragraphs),
                "table_count": len(tables),
                "drawing_count": len(drawing_records),
                "image_count": len(extracted_images),
                "output": str(out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
