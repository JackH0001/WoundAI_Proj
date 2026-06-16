"""標註工作流驗證測試（合成、決定性、無模型）。"""
import sys, os, tempfile, json, numpy as np
import annotation_workflow_validation as awf
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
def box(y0,y1,x0,x1,H=64,W=64):
    m=np.zeros((H,W),bool); m[y0:y1,x0:x1]=True; return m
# 案例：A 初稿準(小修)、B 初稿差(大修)、C 初稿全錯
ed=box(16,48,16,48)                       # 醫師 GT（32x32=1024）
cases=[
 {"name":"A_good","ai_mask":box(16,46,16,48),"edited_mask":ed,"px_per_mm":2.0},   # 接近
 {"name":"B_poor","ai_mask":box(16,32,16,32),"edited_mask":ed,"px_per_mm":2.0},   # 偏小
 {"name":"C_wrong","ai_mask":box(0,8,0,8),"edited_mask":ed,"px_per_mm":2.0},      # 幾乎不重疊
]
q=os.path.join(tempfile.gettempdir(),"awf_queue.jsonl"); open(q,"w").close()
res=awf.validate_workflow(cases, queue_path=q)
rows=res["rows"]; s=res["summary"]
ck("3 筆紀錄", len(rows)==3)
ck("每筆有 correction_iou/draft_dice", all("correction_iou" in r and "draft_dice" in r for r in rows))
ck("A correction_iou > C（A 修得少）", rows[0]["correction_iou"]>rows[2]["correction_iou"])
ck("C 標為高訓練價值(初稿全錯)", rows[2]["high_training_value"] is True)
ck("A 非高訓練價值(初稿準)", rows[0]["high_training_value"] is False)
ck("area_mm2 採用 px_per_mm", abs(rows[0]["area_mm2"]-int(ed.sum())/4.0)<1e-6)
ck("摘要欄位齊全", all(k in s for k in("mean_draft_dice","mean_correction_iou","n_high_training_value","queue_len")))
ck("佇列寫入 3 行", s["queue_len"]==3)
ck("佇列每行合法 JSON", all(json.loads(l) for l in open(q,encoding="utf-8")))
ck("決定性", awf.validate_workflow(cases)["summary"]==awf.validate_workflow(cases)["summary"])
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
