"""Phase 1 #2：規則式嚴重度分數卡 + 治療建議的邊界單元測試（可解釋、臨床可調、輔助用途）。"""
import sys
from clinical_rules import severity_scorecard as sev, treatment as tx, load_params
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)

# ---- 面積門檻邊界（預設 [4,16]；半開區間語意：area<4→0, 4<=area<16→1, area>=16→2） ----
ck("area 3.99 (<4) -> area pt0 -> grade1", sev(3.99,{})["grade"]==1)
ck("area 4.0 (==下界) -> area pt1 -> grade2", sev(4.0,{})["grade"]==2)
ck("area 15.99 (<16) -> area pt1 -> grade2", sev(15.99,{})["grade"]==2)
ck("area 16.0 (==上界) -> area pt2 -> grade3", sev(16.0,{})["grade"]==3)
ck("area 0 -> grade1(min)", sev(0.0,{})["grade"]==1)

# ---- 組織比例邊界（嚴格大於：necrosis>0.2, slough>0.3） ----
ck("necrosis ==0.2 (非>0.2) -> 不計壞死分", sev(2.0,{"necrosis":0.2})["points"]==0)
ck("necrosis 0.2001 (>0.2) -> +2", sev(2.0,{"necrosis":0.2001})["points"]==2)
ck("slough ==0.3 (非>0.3) -> 不計腐肉分", sev(2.0,{"slough":0.3})["points"]==0)
ck("slough 0.3001 (>0.3) -> +1", sev(2.0,{"slough":0.3001})["points"]==1)

# ---- 組織優先序：壞死優先於腐肉 ----
ck("necrosis 與 slough 皆高 -> 取壞死(2分) 非腐肉(1分)", sev(2.0,{"necrosis":0.5,"slough":0.9})["points"]==2)

# ---- grade 上限 / 範圍 ----
ck("area>=16 + necrosis -> pts4 但 grade 上限 4", sev(20.0,{"necrosis":0.5})["grade"]==4)
ck("area>=16 + necrosis -> points==4(未截斷)", sev(20.0,{"necrosis":0.5})["points"]==4)
gr_all=[sev(a,{"necrosis":n,"slough":s})["grade"] for a in (0,4,16,30) for n in (0,0.5) for s in (0,0.5)]
ck("grade 永遠落在 [1,4]", all(1<=g<=4 for g in gr_all))

# ---- 單調性：同組織下面積越大、分數不下降 ----
ck("面積單調：pts(0)<=pts(4)<=pts(16)", sev(0,{})["points"]<=sev(4,{})["points"]<=sev(16,{})["points"])

# ---- 可調參數覆寫 ----
p_tight={"area_thresholds_cm2":[1,2],"necrosis_frac":0.5,"slough_frac":0.6}
ck("自訂 area 門檻 [1,2]：area1.5 -> area pt1", sev(1.5,{},p_tight)["grade"]==2)
ck("自訂 necrosis_frac 0.5：necrosis0.3 不計分", sev(0.5,{"necrosis":0.3},p_tight)["points"]==0)
ck("自訂 necrosis_frac 0.5：necrosis0.6 -> +2", sev(0.5,{"necrosis":0.6},p_tight)["points"]==2)

# ---- 缺鍵 / 容錯 ----
ck("tissue 缺鍵 -> 預設0 不崩潰", sev(2.0,{})["points"]==0)
ck("status == rule_based", sev(5.0,{})["status"]=="rule_based" and tx(1,{})["status"]=="rule_based")

# ---- 決定性 ----
ck("severity 決定性", sev(10.0,{"slough":0.4})==sev(10.0,{"slough":0.4}))
ck("treatment 決定性", tx(3,{"necrosis":0.3})==tx(3,{"necrosis":0.3}))

# ---- 治療建議分支（4 條互斥路徑） ----
ck("tx 壞死 -> 清創/轉診", "清創" in tx(2,{"necrosis":0.3})["recommendation"])
ck("tx 腐肉(無壞死) -> 腐肉/敷料", "腐肉" in tx(2,{"slough":0.4})["recommendation"])
ck("tx 壞死優先於腐肉", "清創評估" in tx(2,{"necrosis":0.3,"slough":0.9})["recommendation"])
ck("tx grade>=3(無壞死腐肉) -> 轉介專科", "專科" in tx(3,{})["recommendation"])
ck("tx 輕症(grade1 無組織) -> 肉芽/濕潤敷料", "肉芽" in tx(1,{})["recommendation"])
ck("tx note 含 需醫師確認", "醫師" in tx(1,{})["note"])

# ---- 參數檔 round-trip ----
import os
pf=load_params(os.path.join(os.path.dirname(os.path.abspath(__file__)),"rules_params.json"))
ck("rules_params.json area_thresholds==[4,16]", pf["area_thresholds_cm2"]==[4,16])
ck("rules_params.json necrosis_frac==0.2", pf["necrosis_frac"]==0.2)
ck("載入參數套用後與預設一致", sev(16.0,{"necrosis":0.3},pf)["grade"]==sev(16.0,{"necrosis":0.3})["grade"])

ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
