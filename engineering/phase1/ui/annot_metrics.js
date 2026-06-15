// 共享標註度量：前端 canvas 與 node 測試共用，必須與 engineering/phase1/annotation_pipeline.py 等價。
(function (global) {
  function countTrue(m){let c=0;for(let i=0;i<m.length;i++) if(m[i]) c++;return c;}
  function iou(a,b){let it=0,un=0;for(let i=0;i<a.length;i++){const x=!!a[i],y=!!b[i];if(x&&y)it++;if(x||y)un++;}return un?it/un:1.0;}
  function xorCount(a,b){let c=0;for(let i=0;i<a.length;i++) if((!!a[i])!==(!!b[i])) c++;return c;}
  function round(x,n){const f=Math.pow(10,n);return Math.round(x*f)/f;}
  function buildRecord(o){
    const ai=o.aiMask, ed=o.editedMask, area_px=countTrue(ed);
    return {schema_version:"1.0", image_id:o.imageId, source:"semi_auto_edit",
      model_id:o.modelId||null, editor_id:o.editorId, area_px,
      area_mm2:(o.pxPerMm? round(area_px/(o.pxPerMm*o.pxPerMm),3):null),
      correction_iou: round(iou(ai,ed),4), pixels_changed: xorCount(ai,ed),
      status:"pending_qc", created_at:o.createdAt||null};
  }
  const api={countTrue,iou,xorCount,round,buildRecord};
  if(typeof module!=="undefined"&&module.exports) module.exports=api; else global.AnnotMetrics=api;
})(typeof window!=="undefined"?window:globalThis);
