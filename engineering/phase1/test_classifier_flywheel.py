# -*- coding: utf-8 -*-
"""方案2:分類飛輪標註回歸測試。"""
import os,sys,numpy as np
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from annotation_pipeline import (make_tissue_annotation_record, aggregate_classifier_manifest,
                                 TISSUE_CLASSES, WOUNDTYPE_CLASSES, MIN_PER_CLASS)

def _cm(code, n=100):
    cm=np.zeros((50,50),np.uint8); cm[:n//50 if n>=50 else 1,:]=code; cm[20:40,20:40]=code; return cm

def test_record_requires_doctor_flag():
    r=make_tissue_annotation_record("img1", _cm(3), "drA", doctor_verified=False)
    assert r["status"]=="pending_qc" and r["doctor_verified"] is False
    r2=make_tissue_annotation_record("img2", _cm(3), "drA", doctor_verified=True, wound_type="diabetic_foot")
    assert r2["status"]=="verified" and r2["wound_type"]=="diabetic_foot"
    assert abs(sum(r2["tissue_frac"].values())-1.0)<1e-6

def test_unknown_woundtype_rejected():
    try:
        make_tissue_annotation_record("x", _cm(3), "drA", doctor_verified=True, wound_type="alien")
        assert False, "應拒絕未知類型"
    except AssertionError as e:
        assert "未知" in str(e)

def test_manifest_only_counts_verified_and_defers():
    recs=[make_tissue_annotation_record(f"i{i}", _cm(3), "drA", doctor_verified=(i%2==0)) for i in range(10)]
    m=aggregate_classifier_manifest(recs)
    assert m["n_total"]==10 and m["n_verified"]==5      # 只算醫師驗證
    assert m["tissue_train_ready"] is False             # 樣本遠不足→誠實延後
    assert "勿硬訓" in m["recommendation"]

def test_manifest_ready_when_enough():
    recs=[]
    for code in (1,2,3):  # necrosis/slough/granulation 各 MIN_PER_CLASS 筆
        recs+=[make_tissue_annotation_record(f"c{code}_{i}", _cm(code), "drA", doctor_verified=True) for i in range(MIN_PER_CLASS)]
    m=aggregate_classifier_manifest(recs)
    assert m["tissue_train_ready"] is True and "可啟動" in m["recommendation"]

if __name__=="__main__":
    for k,fn in list(globals().items()):
        if k.startswith("test_") and callable(fn): fn(); print("PASS",k)
