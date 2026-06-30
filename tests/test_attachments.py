"""Tests for attachment extraction + citation deep-links (schema.py)."""

from __future__ import annotations

from dots_kms_mcp.schema import document_citation, extract_doc_attachments

WEB = "https://knowledge.noorahealth.org"

# A document shaped like the live API: GCS storage objects (PDF + image), an external
# link object with a preview thumbnail, a bare link string, and a translated duplicate.
DOC = {
    "_id": "abc123",
    "metadata": {"contentType": "reports"},
    "main": {
        "uploadImages": [
            {"kind": "storage#object", "contentType": "image/jpeg",
             "originalFilename": "photo.jpeg", "name": "nkms/images/photo.jpeg",
             "publicUrl": "https://storage.googleapis.com/b/photo.jpeg",
             "mediaLink": "https://storage.googleapis.com/download/photo.jpeg", "size": 2048},
        ],
        "uploadDocumentInPDFFormat": [
            {"kind": "storage#object", "contentType": "application/pdf",
             "originalFilename": "Report.pdf", "name": "nkms/pdfs/report.pdf",
             "publicUrl": "https://storage.googleapis.com/b/report.pdf",
             "mediaLink": "https://storage.googleapis.com/download/report.pdf",
             "selfLink": "https://www.googleapis.com/storage/v1/b/report.pdf", "size": 15474060},
        ],
        "linkToDocument": {
            "url": "https://docs.google.com/document/d/DOCID/edit",
            "metadata": {"title": "Strategy Doc",
                         "img": {"url": "https://lh7-us.googleusercontent.com/thumbnail"}},
        },
        "attachALinkToTheDocument": "https://docs.google.com/presentation/d/SLIDES",
    },
    # Same PDF repeated in a translation -> must dedupe by url.
    "translations": {"en": {"main": {"uploadDocumentInPDFFormat": [
        {"kind": "storage#object", "contentType": "application/pdf",
         "originalFilename": "Report.pdf",
         "publicUrl": "https://storage.googleapis.com/b/report.pdf", "size": 15474060},
    ]}}},
}


def test_extracts_all_kinds_pdf_first():
    atts = extract_doc_attachments(DOC)
    assert atts[0]["kind"] == "pdf"  # PDFs sorted first
    assert atts[0]["filename"] == "Report.pdf"
    assert atts[0]["url"] == "https://storage.googleapis.com/b/report.pdf"
    assert atts[0]["content_type"] == "application/pdf"
    assert atts[0]["size"] == 15474060
    assert "uploadDocumentInPDFFormat" in atts[0]["field"]
    assert {a["kind"] for a in atts} == {"pdf", "image", "link"}


def test_dedupes_repeated_url_across_translations():
    atts = extract_doc_attachments(DOC)
    pdfs = [a for a in atts if a["kind"] == "pdf"]
    assert len(pdfs) == 1  # the translated duplicate is collapsed


def test_does_not_emit_download_or_self_links_for_storage_objects():
    urls = [a["url"] for a in extract_doc_attachments(DOC)]
    assert not any("/download/" in u for u in urls)        # mediaLink not separately emitted
    assert not any("/storage/v1/" in u for u in urls)      # selfLink not emitted


def test_link_object_uses_title_and_skips_thumbnail():
    atts = extract_doc_attachments(DOC)
    links = [a for a in atts if a["url"].startswith("https://docs.google.com")]
    doc_link = next(a for a in links if "document/d/DOCID" in a["url"])
    assert doc_link["filename"] == "Strategy Doc"
    # The preview thumbnail inside metadata.img must NOT become its own attachment.
    assert not any("googleusercontent.com" in a["url"] for a in atts)


def test_bare_link_string_captured():
    atts = extract_doc_attachments(DOC)
    assert any(a["url"] == "https://docs.google.com/presentation/d/SLIDES" for a in atts)


def test_kinds_filter():
    atts = extract_doc_attachments(DOC, kinds=["pdf"])
    assert len(atts) == 1 and atts[0]["kind"] == "pdf"


def test_empty_doc_returns_empty_list():
    assert extract_doc_attachments({"_id": "x", "meta": {"title": "no files"}}) == []


def test_pdf_link_string_classified_as_pdf():
    doc = {"main": {"linkToDocument": "https://example.org/files/handbook.pdf"}}
    atts = extract_doc_attachments(doc)
    assert atts[0]["kind"] == "pdf"


# --- citations ---

def test_citation_uses_content_type_hint():
    assert document_citation(DOC, content_type="reports", web_base=WEB) == \
        f"{WEB}/published-page/reports?id=abc123"


def test_citation_falls_back_to_metadata_content_type():
    assert document_citation(DOC, web_base=WEB) == f"{WEB}/published-page/reports?id=abc123"


def test_citation_falls_back_to_meta_kp_content_type():
    doc = {"_id": "x9", "meta": {"kp_content_type": "successStory"}}
    assert document_citation(doc, web_base=WEB) == f"{WEB}/published-page/successStory?id=x9"


def test_citation_none_when_id_missing():
    assert document_citation({"metadata": {"contentType": "reports"}}, web_base=WEB) is None


def test_citation_none_when_content_type_missing():
    assert document_citation({"_id": "x"}, web_base=WEB) is None
