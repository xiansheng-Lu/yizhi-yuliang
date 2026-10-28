"""固定数字分页完整性核对；总量或条数不明确时不能当作完整结果。"""

from typing import Any


def verify_adq_page(
    payload: dict[str, Any], requested_page: int, page_size: int
) -> tuple[int, int]:
    info = payload.get("page_info")
    if not isinstance(info, dict) or any(
        type(info.get(k)) is not int for k in ("page", "page_size", "total_number", "total_page")
    ):
        raise ValueError("响应缺少精确的分页总量依据")
    page, size, total, pages = (
        info[k] for k in ("page", "page_size", "total_number", "total_page")
    )
    if (page, size) != (requested_page, page_size) or not 0 <= total <= 100000 * page_size:
        raise ValueError("响应页码、页大小或总量与请求不匹配")
    expected_pages = (total + size - 1) // size
    if pages != expected_pages and not (total == 0 and pages == 1):
        raise ValueError("分页总数与总条目数不一致")
    rows = payload.get("list")
    if (
        not isinstance(rows, list)
        or page > max(1, pages)
        or len(rows) != max(0, min(size, total - (page - 1) * size))
    ):
        raise ValueError("分页出现缺页或条目计数不符")
    return total, pages
