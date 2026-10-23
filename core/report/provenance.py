import re
from datetime import timezone
from xml.sax.saxutils import escape

from reportlab.pdfbase.pdfdoc import XMP

CLOUDEXIT_NS = "https://cloudexit.escapecloud.io/ns/report/1.0/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
# XML 1.0 has no escape for these, so a name carrying one would break the packet.
XML_INVALID = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f" + chr(0xFFFE) + chr(0xFFFF) + "]"
)


def _utc(value):
    return value.astimezone(timezone.utc)


def _xml(value):
    return escape(XML_INVALID.sub("", str(value if value is not None else "")))


def attach_metadata(canvas, provenance):
    generated_at = _utc(provenance.generated_at)
    # The Info dict dates follow the generation time, not the render clock.
    canvas.setDateFormatter(
        lambda *_local: generated_at.strftime("D:%Y%m%d%H%M%S+00'00'")
    )
    # Bytes, because ReportLab would encode a str as PDFDocEncoding, which has
    # no BOM and no non-Latin text; XMP is UTF-8.
    xmp = XMP(creator=lambda document: xmp_packet(document.info, provenance).encode())
    # An empty filter list keeps the packet uncompressed, so tools that scan
    # the raw file for XMP still find it.
    xmp.filters = ()
    canvas._doc.Catalog.Metadata = xmp


def xmp_packet(info, provenance):
    generated_at = _utc(provenance.generated_at)
    stamp = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {
        "RunID": provenance.run_id,
        "CollectorVersion": provenance.collector_version,
        "CatalogueVersion": provenance.catalogue_version,
        "CatalogueSHA256": provenance.catalogue_sha256,
        "Uploaded": str(provenance.uploaded).lower(),
        "GeneratedAt": generated_at.isoformat(),
    }
    custom = "".join(
        f"<cloudexit:{field}>{_xml(value)}</cloudexit:{field}>"
        for field, value in fields.items()
        if value
    )
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        f'<rdf:RDF xmlns:rdf="{RDF_NS}">'
        '<rdf:Description rdf:about=""'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:pdf="http://ns.adobe.com/pdf/1.3/"'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
        f' xmlns:cloudexit="{CLOUDEXIT_NS}">'
        "<dc:format>application/pdf</dc:format>"
        "<dc:title><rdf:Alt>"
        f'<rdf:li xml:lang="x-default">{_xml(info.title)}</rdf:li>'
        "</rdf:Alt></dc:title>"
        "<dc:description><rdf:Alt>"
        f'<rdf:li xml:lang="x-default">{_xml(info.subject)}</rdf:li>'
        "</rdf:Alt></dc:description>"
        f"<pdf:Keywords>{_xml(info.keywords)}</pdf:Keywords>"
        f"<xmp:CreatorTool>{_xml(info.creator)}</xmp:CreatorTool>"
        f"<xmp:CreateDate>{stamp}</xmp:CreateDate>"
        f"<xmp:ModifyDate>{stamp}</xmp:ModifyDate>"
        f"<xmp:MetadataDate>{stamp}</xmp:MetadataDate>"
        f"{custom}"
        "</rdf:Description>"
        "</rdf:RDF>"
        "</x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
