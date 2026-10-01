from pathlib import Path

from lxml import etree

from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.project_index import ProjectIndex


def validate_staging(
    staging_root: Path, index: ProjectIndex
) -> tuple[Diagnostic, ...]:
    del index
    diagnostics: list[Diagnostic] = []
    for path in sorted(staging_root.rglob("*.xml")):
        relative = path.relative_to(staging_root).as_posix()
        try:
            tree = etree.parse(str(path))
        except etree.XMLSyntaxError as error:
            diagnostics.append(
                Diagnostic(
                    code="xml.parseable",
                    severity=Severity.ERROR,
                    message=str(error),
                    path=relative,
                    rule_id="xml.parseable",
                    rule_version=1,
                )
            )
            continue
        for text in tree.xpath("//text[not(@autoSize='none')]"):
            diagnostics.append(
                Diagnostic(
                    code="text.fixed-size",
                    severity=Severity.ERROR,
                    message="FGUI text must use autoSize=none",
                    path=relative,
                    node_id=text.attrib.get("id"),
                    rule_id="text.fixed-size",
                    rule_version=1,
                )
            )
    return tuple(diagnostics)


def has_errors(diagnostics: tuple[Diagnostic, ...]) -> bool:
    return any(item.severity is Severity.ERROR for item in diagnostics)
