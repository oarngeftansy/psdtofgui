from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
DEMO = REPO_ROOT / "docs" / "demos" / "writer-review-workbench.html"


def test_writer_review_workbench_demo_is_self_contained() -> None:
    html = DEMO.read_text("utf-8")
    assert "https://" not in html and "http://" not in html
    assert "<link" not in html.lower()
    assert "<img" not in html.lower()
    assert 'content="width=640' in html
    assert html.count('class="step-screen') == 3
    assert 'data-go-step="automatic"' in html
    assert 'data-go-step="review"' in html
    assert 'data-go-step="confirm"' in html
    assert html.count('class="review-record"') >= 2
    assert 'id="copy-to-figma"' in html
    assert 'class="figma-canvas-demo"' in html
    assert 'id="engineering-details"' in html
    for label in (
        "自动转换",
        "建议审核",
        "确认下载",
        "Figma 原图",
        "FairyGUI 结果",
        "复制到 Figma 审核区",
        "FairyGUI 待审核",
        "工程详情",
    ):
        assert label in html


def test_writer_review_workbench_demo_fits_and_moves_in_real_edge(
    tmp_path: Path,
) -> None:
    node = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("requires the Codex Node runtime and local Microsoft Edge")
    playwright = node.parents[1] / "node_modules/playwright-core"
    screenshot = tmp_path / "workbench-review.png"
    script = f"""
const {{ chromium }} = require({json.dumps(str(playwright))});
(async () => {{
  const browser = await chromium.launch({{ executablePath: {json.dumps(str(edge))}, headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 640, height: 800 }}, deviceScaleFactor: 1 }});
  await page.goto({json.dumps(DEMO.resolve().as_uri())});
  const initialStep = await page.locator('.plugin-window').getAttribute('data-step');
  await page.click('.primary[data-go-step="review"]');
  await page.waitForTimeout(300);
  const measurement = await page.evaluate(() => {{
    const box = (selector) => document.querySelector(selector).getBoundingClientRect().toJSON();
    const visible = (element) => element.getClientRects().length > 0;
    const scrollCount = [...document.querySelectorAll('*')].filter((element) => {{
      if (!visible(element)) return false;
      const style = getComputedStyle(element);
      return ['auto', 'scroll'].includes(style.overflowY) && element.scrollHeight > element.clientHeight;
    }}).length;
    return {{
      root: box('.plugin-window'),
      scroll: box('.step-screen:not([hidden]) .step-scroll'),
      footer: box('.action-footer'),
      scrollCount,
      documentWidth: document.documentElement.scrollWidth,
      previews: [...document.querySelectorAll('.step-screen:not([hidden]) .preview-canvas')]
        .filter((element) => element.getClientRects().length > 0)
        .map((element) => element.getBoundingClientRect().toJSON()),
    }};
  }});
  await page.screenshot({{ path: {json.dumps(str(screenshot))} }});
  await page.click('[data-review-nav="next"]');
  await page.waitForTimeout(220);
  const reviewItemAfterNext = (await page.locator('#review-counter').textContent()).trim();
  await page.check('#acknowledge');
  await page.click('#copy-to-figma');
  await page.waitForTimeout(320);
  const copyState = await page.locator('.figma-canvas-demo').getAttribute('data-copy-state');
  const copyMessage = (await page.locator('#copy-message').textContent()).trim();
  await page.click('[data-go-step="confirm"]');
  await page.click('#engineering-details');
  const detailsOpen = await page.locator('#engineering-details').getAttribute('aria-expanded');
  const downloadDisabled = await page.locator('#download').isDisabled();
  await browser.close();
  process.stdout.write(JSON.stringify({{
    initialStep, measurement, reviewItemAfterNext, copyState, copyMessage, detailsOpen, downloadDisabled
  }}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        [str(node), "-e", script],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    measurement = result["measurement"]
    assert result["initialStep"] == "automatic"
    assert measurement["root"]["width"] == 640
    assert measurement["root"]["height"] == 800
    assert measurement["documentWidth"] == 640
    assert measurement["scrollCount"] == 1
    assert measurement["footer"]["top"] >= measurement["scroll"]["bottom"]
    assert len(measurement["previews"]) == 2
    assert all(item["width"] >= 220 for item in measurement["previews"])
    assert all(item["height"] >= 260 for item in measurement["previews"])
    assert len({round(item["width"], 2) for item in measurement["previews"]}) == 1
    assert len({round(item["top"], 2) for item in measurement["previews"]}) == 1
    assert result["reviewItemAfterNext"] == "2 / 2"
    assert result["copyState"] == "copied"
    assert "当前画板右侧" in result["copyMessage"]
    assert result["detailsOpen"] == "true"
    assert result["downloadDisabled"] is False
