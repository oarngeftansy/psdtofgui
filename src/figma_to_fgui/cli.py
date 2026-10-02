import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
import time
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

import typer
from pydantic import ValidationError

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.component_mapping import load_mapping_catalog, validate_mapping_catalog
from figma_to_fgui.data_policy import private_data_violations
from figma_to_fgui.fgui_asset_payloads import (
    MAX_ASSET_PAYLOAD_BYTES,
    MAX_TOTAL_ASSET_PAYLOAD_BYTES,
)
from figma_to_fgui.fgui_new_project_build import NewProjectBuildError, build_new_project
from figma_to_fgui.fgui_new_project_models import (
    AssetPayload,
    AssetPayloadSet,
    NewProjectConfig,
)
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_models import (
    FGUIPlanDocument,
    FGUIPlanV1Document,
    migrate_plan_v1_without_components,
)
from figma_to_fgui.fgui_plan_validate import canonical_plan_bytes, validate_fgui_plan
from figma_to_fgui.models import Severity
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.pipeline import ConversionRequest, convert
from figma_to_fgui.project_index import index_project
from figma_to_fgui.rules import load_rules
from figma_to_fgui.uir_compile import compile_uir
from figma_to_fgui.uir_models import UIRDocument
from figma_to_fgui.uir_validate import canonical_uir_bytes, validate_uir
from figma_to_fgui.validate import has_errors, validate_staging

# Plain Click/Typer diagnostics are deterministic across terminal widths. Rich
# wraps long option names in CI, making the same validation error render
# differently even though the underlying parameter is unchanged.
app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
agent_app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
app.add_typer(agent_app, name="agent")

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
