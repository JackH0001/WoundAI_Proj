"""遮罩精修（可調參數）：去除 ROI 裁切框邊偽影、細刺、孔洞與離散小塊，提升量測精度。
參數：open_k(去刺) / close_k(填洞) / border_px(ROI 邊界抑制) / keep_largest / min_area_frac。"""
import numpy as np
import cv2
def refine(mask, roibox=None, open_k=5, close_k=15, border_px=4, keep_largest=True, min_area_frac=0.0):
    m = np.asarray(mask, np.uint8)
    if m.sum() == 0: return m.astype(bool)
    if open_k > 1:  m = cv2.morphologyEx(m, cv2.MORPH_OPEN,  np.ones((open_k, open_k), np.uint8))
    if close_k > 1: m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((close_k, close_k), np.uint8))
    if roibox is not None and border_px > 0:        # 抑制貼 ROI 框邊之偽影
        x0, y0, x1, y1 = [int(v) for v in roibox]
        b = border_px
        m[max(0,y0):y0+b, :] = 0; m[max(0,y1-b):y1, :] = 0
        m[:, max(0,x0):x0+b] = 0; m[:, max(0,x1-b):x1] = 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1: return m.astype(bool)
    areas = stats[1:, cv2.CC_STAT_AREA]; imgA = m.shape[0]*m.shape[1]
    if keep_largest:
        idx = 1 + int(np.argmax(areas)); m = (lab == idx).astype(np.uint8)
    elif min_area_frac > 0:                          # 否則濾掉過小塊
        keep = np.zeros_like(m)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= imgA*min_area_frac: keep[lab == i] = 1
        m = keep
    return m.astype(bool)
