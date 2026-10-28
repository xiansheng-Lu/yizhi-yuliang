"""富文本输入按纯文本转义，不允许脚本URL或事件处理器执行。"""

from yuliang.modules.analysis.drafts_service import preview_html


def test_preview_escapes_title_text_and_metric_definitions() -> None:
    draft = dict(
        title="<script>alert(1)</script>",
        query=dict(startDate="2026-10-01", endDate="2026-10-07"),
        coverage="<img src=x>",
        analysisId="source",
        version=1,
        metrics=[dict(id="ads.spend", name="<svg onload=x>", value=0)],
        modules=[
            dict(
                visible=True,
                title="<iframe>",
                text='<a href="javascript:x">x</a>',
                metricIds=["ads.spend"],
            )
        ],
    )
    html = preview_html(draft)
    assert "<script>" not in html and "<iframe>" not in html and "<svg " not in html
    assert (
        "&lt;script&gt;" in html and "&lt;iframe&gt;" in html and "Content-Security-Policy" in html
    )
