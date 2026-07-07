# -*- coding: utf-8 -*-
"""雙軌路由端到端整合/回歸測試(WoundAI)。
用 5 張臨床照的快取機率(student/wsm/au=A∪U)重現真實 client 流程:
  端上 student 主遮罩 + 與 wsm 算分歧度 IoU → <ESCALATE_IOU 判難例 → 上雲 A∪U。
不需載入模型(快、確定性),可掛 CI 當回歸守門。對應 dual_track_router.route()。"""
import os, numpy as np
from PIL import Image

ROUTE5 = os.environ.get("ROUTE5", "")  # 本機驗證資料(不隨 repo 發布)；設 ROUTE5 環境變數指向 批次驗證工具/route5
if not ROUTE5 or not os.path.isdir(ROUTE5):
    print("SKIP: ROUTE5 資料集不存在(本測試需本機 批次驗證工具/route5，非 repo 內容)。設環境變數 ROUTE5 後重跑。")
    raise SystemExit(0)
GTDIR = os.environ.get("GTDIR",
    "/sessions/nifty-sweet-edison/mnt/dev/WoundAI_weights_archive/test_images/方形校正貼紙範例/labels_correct")
ESCALATE_IOU, STU_THR, WSM_THR, CLOUD_THR = 0.50, 0.40, 0.50, 0.40
NAMES = ["Bedsore_方形校正貼紙範例","Bedsore_02_方形校正貼紙範例",
         "Body_chronic_wound_校正貼紙範例","Burn_ChronicWound","Foot_chronic_ulcer_校正貼紙"]

def _iou(a,b):
    a=a.astype(bool);b=b.astype(bool);u=(a|b).sum();return float((a&b).sum()/u) if u else 1.0
def _dice(p,g):
    p=p.astype(bool);g=g.astype(bool);s=p.sum()+g.sum();return 1.0 if s==0 else 2*(p&g).sum()/s

def run():
    rows=[]; escalated=0
    for nm in NAMES:
        stu=np.load(os.path.join(ROUTE5,nm+"_stu.npy")).astype(np.float32)
        wsm=np.load(os.path.join(ROUTE5,nm+"_wsm.npy")).astype(np.float32)
        au =np.load(os.path.join(ROUTE5,nm+"_au.npy")).astype(np.float32)
        g=(np.asarray(Image.open(os.path.join(GTDIR,nm+".png")).convert("L"))>127).astype(np.uint8)
        sm, wm = stu>STU_THR, wsm>WSM_THR
        dis=_iou(sm,wm)                                  # 端上分歧度
        if dis < ESCALATE_IOU:                           # 判難 → 上雲 A∪U
            route="cloud"; mask=au>CLOUD_THR; escalated+=1
        else:
            route="ondevice"; mask=sm
        d_route=_dice(mask,g); d_stu=_dice(sm,g); d_cloud=_dice(au>CLOUD_THR,g)
        rows.append((nm,dis,route,d_stu,d_cloud,d_route))
    return rows, escalated

def test_dual_track():
    rows, esc = run()
    print(f"\n{'image':<14}{'分歧IoU':>8}{'route':>10}{'student':>9}{'cloud':>8}{'最終':>8}")
    routed=[]
    for nm,dis,route,ds,dc,dr in rows:
        print(f"{nm[:12]:<14}{dis:>8.2f}{route:>10}{ds:>9.3f}{dc:>8.3f}{dr:>8.3f}")
        routed.append(dr)
    mean=float(np.mean(routed)); stu_mean=float(np.mean([r[3] for r in rows]))
    print(f"{'平均':<14}{'':>8}{'':>10}{stu_mean:>9.3f}{'':>8}{mean:>8.3f}")
    print(f"escalate 上雲:{esc}/{len(rows)}  端上純 student 平均={stu_mean:.3f} → 路由後={mean:.3f}")
    # 回歸斷言
    assert mean >= 0.88, f"路由後平均 Dice {mean:.3f} < 0.88 回歸門檻"
    assert mean > stu_mean, "路由未優於純端上 student"
    hard=[r for r in rows if r[3]<0.6]                   # student 表現差者
    assert all(r[2]=="cloud" for r in hard), "有難例(student<0.6)未被 escalate 上雲"
    print("PASS: 路由後≥0.88、優於純端上、難例皆上雲")
    return None  # pytest: 不回傳值

if __name__=="__main__":
    test_dual_track()
