const A = require("./annot_metrics.js"); const fs = require("fs"), path = require("path");
const W=10, N=W*W, ai=new Uint8Array(N), ed=new Uint8Array(N);
for(let r=2;r<6;r++) for(let c=2;c<6;c++) ai[r*W+c]=1;   // 4x4 = 16
for(let r=2;r<8;r++) for(let c=2;c<6;c++) ed[r*W+c]=1;   // 6x4 = 24
const rec=A.buildRecord({imageId:"img1",aiMask:ai,editedMask:ed,editorId:"dr_a",modelId:"segmentation.wsm",pxPerMm:2.0});
let pass=0,total=0; const ck=(n,c)=>{total++; if(c){pass++;console.log("PASS",n);} else console.log("FAIL",n);};
ck("area_px==24", rec.area_px===24);
ck("area_mm2==6.0", rec.area_mm2===6.0);
ck("correction_iou==0.6667", rec.correction_iou===0.6667);
ck("pixels_changed==8", rec.pixels_changed===8);
ck("status pending_qc", rec.status==="pending_qc");
const ej=path.join(__dirname,"expected.json");
if(fs.existsSync(ej)){const e=JSON.parse(fs.readFileSync(ej));
  ck("xlang area_px==py", rec.area_px===e.area_px);
  ck("xlang correction_iou==py", rec.correction_iou===e.correction_iou);
  ck("xlang pixels_changed==py", rec.pixels_changed===e.pixels_changed);
} else console.log("(expected.json 缺，略過跨語言比對)");
console.log(`\n${pass}/${total} PASS`); process.exit(pass===total?0:1);
