"""模板匹配（纯 Python 实现，针对屏幕截图场景优化）。

原理
----
1. 使用屏幕的绿通道作为灰度近似：``gray = raw_bgra[1::4]``（C 速度切片）。
2. 把灰度量化到 16 级（``v & 0xF0``），容忍少量颜色/抗锯齿差异。
3. 在模板中挑选"水平边缘最多"的若干行作为锚点行，用 ``bytes.find`` 在
   屏幕灰度缓冲中做 C 速度的整行搜索，得到候选位置。
4. 对候选位置逐行校验，按"不匹配像素比例"计算相似度，返回最佳结果。
5. 若精确锚点搜索失败（目标画面有轻微变化），回退到 4 级粗量化锚点搜索，
   再回到 16 级校验，从而兼顾速度与容错。

相比 OpenCV matchTemplate，本实现没有第三方依赖，在 2560x1440 全屏上
一次查找通常在几毫秒级别；对"从屏幕截取的 UI 图标/按钮"识别效果最好。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# 灰度量化表：16 级（容差约 ±7）
QUANT_TABLE = bytes((v & 0xF0) for v in range(256))
# 灰度粗量化表：4 级（用于降级容错匹配）
COARSE_TABLE = bytes((v & 0xC0) for v in range(256))


def quantize(data: bytes) -> bytes:
    """把灰度字节量化到 16 级，提升对微小色差的容忍度。"""
    return data.translate(QUANT_TABLE)


@dataclass
class TemplateQuality:
    ok: bool
    edge_ratio: float
    levels: int
    message: str


def check_template(gray: bytes, width: int, height: int) -> TemplateQuality:
    """评估模板是否适合做匹配（特征是否足够）。"""
    if width < 3 or height < 3:
        return TemplateQuality(False, 0.0, 0, "模板图片太小，请至少框选 16×16 像素的区域")
    total = width * height
    edges = 0
    for y in range(height):
        row = gray[y * width : (y + 1) * width]
        edges += sum(1 for i in range(1, width) if row[i] != row[i - 1])
    edge_ratio = edges / total
    levels = len(set(gray))
    if levels <= 1:
        return TemplateQuality(False, edge_ratio, levels, "模板几乎是纯色，无法可靠识别，请重新框选")
    if edge_ratio < 0.01:
        return TemplateQuality(
            False,
            edge_ratio,
            levels,
            "模板特征偏少（对比度较低），识别可能不稳定，建议框选包含文字/图标/边框的区域",
        )
    return TemplateQuality(True, edge_ratio, levels, "模板特征良好")


@dataclass
class Match:
    """一次匹配结果（坐标为该屏幕缓冲的局部坐标）。"""

    score: float
    x: int
    y: int
    width: int
    height: int
    elapsed_ms: float = 0.0
    candidates: int = 0

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def _region_buffer(
    screen_gray: bytes,
    screen_w: int,
    screen_h: int,
    region: tuple[int, int, int, int] | None,
) -> tuple[bytes, int, int, int, int]:
    """返回 (缓冲, 宽, 高, 区域x, 区域y)；region 为 None 表示整屏。"""
    if region is None:
        return screen_gray, screen_w, screen_h, 0, 0
    x0, y0, rw, rh = (int(v) for v in region)
    x0 = max(0, min(x0, screen_w - 1))
    y0 = max(0, min(y0, screen_h - 1))
    rw = max(1, min(rw, screen_w - x0))
    rh = max(1, min(rh, screen_h - y0))
    if x0 == 0 and y0 == 0 and rw == screen_w and rh == screen_h:
        return screen_gray, screen_w, screen_h, 0, 0
    rows = [
        screen_gray[(y0 + r) * screen_w + x0 : (y0 + r) * screen_w + x0 + rw]
        for r in range(rh)
    ]
    return b"".join(rows), rw, rh, x0, y0


def _row_edges(row: bytes) -> int:
    return sum(1 for i in range(1, len(row)) if row[i] != row[i - 1])


def _segments(row: bytes, tw: int) -> list[tuple[int, bytes]]:
    """把模板行切成若干段，返回 [(段起始x, 段数据)]。

    分段搜索比"整行精确匹配"更能容忍目标画面里的少量像素变化。
    """
    seg_len = min(128, max(16, tw // 2))
    if seg_len >= tw:
        starts = [0]
    else:
        starts = sorted({0, (tw - seg_len) // 2, tw - seg_len})
    result: list[tuple[int, bytes]] = []
    for start in starts:
        segment = row[start : start + seg_len]
        if _row_edges(segment) > 0:  # 纯色段没有辨识度，跳过
            result.append((start, segment))
    if not result:
        # 全部是纯色段时退化为整行（可能高度重复，靠校验兜底）
        result.append((0, row))
    return result


def _verify(
    verify_buf: bytes,
    rw: int,
    tpl_rows: list[bytes],
    tw: int,
    th: int,
    anchor: int,
    x: int,
    top_y: int,
    allowed: int,
) -> float | None:
    """逐行校验候选位置，返回相似度；超过允许误差返回 None。"""
    top = top_y * rw + x
    mism = 0
    for r in range(th):
        if r == anchor:
            continue
        off = top + r * rw
        got = verify_buf[off : off + tw]
        want = tpl_rows[r]
        if got != want:
            mism += sum(1 for i in range(tw) if got[i] != want[i])
            if mism > allowed:
                return None
    return 1.0 - mism / (tw * th)


def _search_pass(
    search_buf: bytes,
    rw: int,
    rh: int,
    anchors: list[int],
    search_rows: list[bytes],
    verify_buf: bytes,
    tpl_rows: list[bytes],
    tw: int,
    th: int,
    allowed: int,
    max_candidates: int,
    ox: int,
    oy: int,
) -> tuple[Match | None, int]:
    """用锚点行中的若干段搜索并校验；返回 (最佳结果, 校验的候选数)。"""
    best: Match | None = None
    candidates = 0
    remaining = max_candidates
    seen: set[tuple[int, int]] = set()
    for anchor in anchors:
        if remaining <= 0:
            break
        anchor_row = search_rows[anchor]
        anchor_found = False
        for seg_x, segment in _segments(anchor_row, tw):
            if remaining <= 0:
                break
            pos = 0
            while remaining > 0:
                pos = search_buf.find(segment, pos)
                if pos < 0:
                    break
                hit_x = pos % rw
                hit_row = (pos - hit_x) // rw
                remaining -= 1
                if hit_x >= seg_x and hit_x + len(segment) <= rw:
                    x = hit_x - seg_x
                    top_y = hit_row - anchor
                    if 0 <= top_y <= rh - th and x + tw <= rw:
                        key = (x, top_y)
                        if key in seen:
                            pos += 1
                            continue
                        seen.add(key)
                        candidates += 1
                        score = _verify(
                            verify_buf, rw, tpl_rows, tw, th, anchor, x, top_y, allowed
                        )
                        if score is not None:
                            if best is None or score > best.score:
                                best = Match(
                                    score=score,
                                    x=x + ox,
                                    y=top_y + oy,
                                    width=tw,
                                    height=th,
                                )
                                anchor_found = True
                                if score >= 0.999:
                                    return best, candidates
                pos += 1
        if anchor_found and best is not None and best.score >= 0.5:
            # 当前锚点行已经能匹配上，不再尝试其它锚点行
            break
    return best, candidates


def find_template(
    screen_gray: bytes,
    screen_w: int,
    screen_h: int,
    tpl_gray: bytes,
    tpl_w: int,
    tpl_h: int,
    region: tuple[int, int, int, int] | None = None,
    threshold: float = 0.90,
    max_candidates: int = 400,
) -> Match | None:
    """在屏幕灰度图中查找模板。

    参数
    ----
    screen_gray : 长度 screen_w*screen_h 的灰度字节（绿通道）
    region      : 搜索区域 (x, y, w, h)，局部屏幕坐标；None = 整个屏幕
    threshold   : 相似度阈值 0~1，例如 0.9 表示允许 10% 像素不一致
    """
    started = time.perf_counter()
    if tpl_w < 3 or tpl_h < 3 or tpl_w > screen_w or tpl_h > screen_h:
        return None
    if len(screen_gray) < screen_w * screen_h or len(tpl_gray) < tpl_w * tpl_h:
        return None

    buf, rw, rh, ox, oy = _region_buffer(screen_gray, screen_w, screen_h, region)
    if tpl_w > rw or tpl_h > rh:
        return None

    region_q = quantize(buf)
    tpl_q = quantize(tpl_gray[: tpl_w * tpl_h])
    rows16 = [tpl_q[r * tpl_w : (r + 1) * tpl_w] for r in range(tpl_h)]
    edge_counts = [_row_edges(row) for row in rows16]
    order = sorted(range(tpl_h), key=lambda i: edge_counts[i], reverse=True)
    anchors16 = [i for i in order[:3] if edge_counts[i] > 0]
    if not anchors16:
        # 模板几乎是纯色/纯横条，不具备可匹配特征
        return None

    total = tpl_w * tpl_h
    allowed = max(0, int(total * (1.0 - max(0.0, min(1.0, threshold)))))
    best: Match | None = None
    candidates = 0

    # 第一遍：16 级量化精确锚点（最快，适合模板与画面完全一致的场景）
    best, used = _search_pass(
        region_q,
        rw,
        rh,
        anchors16,
        rows16,
        region_q,
        rows16,
        tpl_w,
        tpl_h,
        allowed,
        max_candidates,
        ox,
        oy,
    )
    candidates += used
    if best is not None and best.score >= max(threshold, 0.999):
        best.elapsed_ms = (time.perf_counter() - started) * 1000
        best.candidates = candidates
        return best

    # 第二遍：4 级粗量化锚点（容忍画面轻微变化后再用 16 级校验）
    if best is None or best.score < threshold:
        coarse_buf = buf.translate(COARSE_TABLE)
        coarse_tpl = tpl_gray[: tpl_w * tpl_h].translate(COARSE_TABLE)
        coarse_rows = [coarse_tpl[r * tpl_w : (r + 1) * tpl_w] for r in range(tpl_h)]
        coarse_best, used2 = _search_pass(
            coarse_buf,
            rw,
            rh,
            anchors16,
            coarse_rows,
            region_q,
            rows16,
            tpl_w,
            tpl_h,
            allowed,
            max(200, max_candidates),
            ox,
            oy,
        )
        candidates += used2
        if coarse_best is not None and (best is None or coarse_best.score > best.score):
            best = coarse_best

    if best is None:
        return None
    best.elapsed_ms = (time.perf_counter() - started) * 1000
    best.candidates = candidates
    return best if best.score >= threshold else None
