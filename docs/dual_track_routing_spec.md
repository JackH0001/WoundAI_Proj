# 雙軌分割路由 — 介面規格(Dual-Track Routing Spec)

> 模組:`engineering/phase2/dual_track_router.py`。已驗證(5 臨床照):端上 student 0.656、雲端 A∪U 0.924、**路由結果 0.900,雲端僅跑 3/5**。

## 1. 流程
1. **端上(on-device)**:student(13MB)推論主遮罩 + wsm(8.5MB)推論 → 計算兩者 **分歧度 IoU**。
2. **判難**:`IoU(student, wsm) < ESCALATE_IOU(預設 0.50)` → 視為難例。
3. **路由**:難例 → 呼叫雲端 **A∪U(a_unet⊕unet++ 機率融合,thr0.4)**;否則用端上 student 結果。
4. 前處理參數一律由 SSOT `preprocessing.json` 提供(student=imagenet/RGB/NCHW/256/0.4;wsm=[0,1]/BGR/224/0.5;a_unet/unetpp=[-1,1]/RGB/256)。

## 2. 端上 API(各端本地呼叫)
`segment_ondevice(img, student_sess, wsm_sess) -> {prob, mask, disagreement_iou, wsm_mask}`
`route(img, ondevice, cloud_fn, escalate_iou=0.5) -> {route, mask, prob, disagreement_iou, reason}`
- `cloud_fn` 為延遲呼叫(只有判難時才真的打雲端,省算力/流量)。

## 3. 雲端升級 API(REST)
```
POST /api/v1/segment/escalate
Headers: Authorization: Bearer <jwt>
Body(multipart): image=<jpg/png>; [calibration_data=<json>]; [ondevice_disagreement=<float>]
Resp 200(json):
{
  "mask_png_b64": "...",          # 256或原圖尺寸二值遮罩
  "prob_rle": [...],              # 選配:機率(壓縮)
  "model": "ensemble.AU",         # 用的雲端模型
  "model_version": "2026-06",     # 來自 model_registry
  "area_hint": null,              # 面積由端上校正算;雲端僅回遮罩
  "route": "cloud",
  "note": "A∪U 機率融合 thr0.4"
}
```
- 失敗(模型缺/逾時)→ 回端上結果(graceful degrade),不偽造。
- 隱私:影像走 TLS;雲端不長存原圖(僅暫存推論);PHI 去識別。

## 4. 設定(集中管理)
| 參數 | 預設 | 說明 |
|---|---|---|
| ESCALATE_IOU | 0.50 | 端上分歧度門檻;↑更常上雲(更準、更耗雲) |
| CLOUD_W | (0.5,0.5) | A∪U 融合權重 |
| CLOUD_THR | 0.40 | 雲端二值門檻 |
- 建議集中於 SSOT(`calibration.routing`)或 model_registry,各端讀同一份。

## 5. 驗收
- [ ] 端上分歧度能區分易/難(實測易 0.72–0.79、難 0.10–0.23)。
- [ ] 難例上雲後 Dice 顯著回升(Body 0.44→0.92、Foot 0.36→0.92)。
- [ ] 雲端缺模型→graceful degrade 回端上。
- [ ] 路由比例與雲端用量在監控面板可見(成本/延遲)。

## 6. 各端 client 接線片段(端上判難 → 上雲)
> 共同流程:端上算 `student_mask`、`wsm_mask` → `iou = |A∩B|/|A∪B|` → `iou < 0.50` 時 POST 影像到 escalate 端點,解 base64 遮罩取代端上結果;否則用端上 student。面積一律由端上 ArUco 校正計算(雲端只回遮罩)。

### 6.1 Android(Kotlin)
```kotlin
fun disagreementIoU(a: BooleanArray, b: BooleanArray): Float {
    var inter = 0; var uni = 0
    for (i in a.indices) { val x=a[i]; val y=b[i]; if (x||y) uni++; if (x&&y) inter++ }
    return if (uni==0) 1f else inter.toFloat()/uni
}
// student/wsm 推論後:
val iou = disagreementIoU(studentMask, wsmMask)
val finalMask: BooleanArray = if (iou < 0.50f) {
    try { escalateToCloud(imageBytes, jwt) }   // 見下,失敗回端上
    catch (e: Exception) { studentMask }       // graceful degrade
} else studentMask

// OkHttp multipart → /api/v1/segment/escalate
fun escalateToCloud(jpeg: ByteArray, jwt: String): BooleanArray {
    val body = MultipartBody.Builder().setType(MultipartBody.FORM)
        .addFormDataPart("image","wound.jpg",
            jpeg.toRequestBody("image/jpeg".toMediaType())).build()
    val req = Request.Builder().url("$BASE/api/v1/segment/escalate")
        .header("Authorization","Bearer $jwt").post(body).build()
    OkHttpClient().newCall(req).execute().use { r ->
        if (!r.isSuccessful) throw IOException("escalate ${r.code}")
        val j = JSONObject(r.body!!.string())
        val png = Base64.decode(j.getString("mask_png_b64"), Base64.DEFAULT)
        val bmp = BitmapFactory.decodeByteArray(png,0,png.size)
        return bitmapToBoolMask(bmp, 0.5)      // >127 視為前景
    }
}
```

### 6.2 iOS(Swift)
```swift
func disagreementIoU(_ a: [Bool], _ b: [Bool]) -> Float {
    var inter = 0, uni = 0
    for i in a.indices { if a[i]||b[i] {uni+=1}; if a[i]&&b[i] {inter+=1} }
    return uni==0 ? 1 : Float(inter)/Float(uni)
}
let iou = disagreementIoU(studentMask, wsmMask)
if iou < 0.50 {
    escalateToCloud(jpeg: jpegData, jwt: token) { cloudMask in
        finalMask = cloudMask ?? studentMask    // 失敗回端上
    }
} else { finalMask = studentMask }

func escalateToCloud(jpeg: Data, jwt: String, done: @escaping ([Bool]?)->Void) {
    var req = URLRequest(url: URL(string: "\(base)/api/v1/segment/escalate")!)
    req.httpMethod = "POST"
    req.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")
    let b = "Boundary-\(UUID().uuidString)"
    req.setValue("multipart/form-data; boundary=\(b)", forHTTPHeaderField: "Content-Type")
    var body = Data()
    body.append("--\(b)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"wound.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".data(using:.utf8)!)
    body.append(jpeg); body.append("\r\n--\(b)--\r\n".data(using:.utf8)!)
    req.httpBody = body
    URLSession.shared.dataTask(with: req) { d,_,_ in
        guard let d=d,
              let j=try? JSONSerialization.jsonObject(with:d) as? [String:Any],
              let s=j["mask_png_b64"] as? String,
              let png=Data(base64Encoded:s) else { done(nil); return }
        done(pngToBoolMask(png, 0.5))
    }.resume()
}
```

### 6.3 Windows(C#)
```csharp
static float DisagreementIoU(bool[] a, bool[] b) {
    int inter=0, uni=0;
    for (int i=0;i<a.Length;i++){ if(a[i]||b[i])uni++; if(a[i]&&b[i])inter++; }
    return uni==0 ? 1f : (float)inter/uni;
}
float iou = DisagreementIoU(studentMask, wsmMask);
bool[] finalMask = studentMask;
if (iou < 0.50f) {
    try { finalMask = await EscalateToCloud(jpegBytes, jwt); }
    catch { finalMask = studentMask; }                 // graceful degrade
}

static async Task<bool[]> EscalateToCloud(byte[] jpeg, string jwt) {
    using var http = new HttpClient();
    http.DefaultRequestHeaders.Authorization = new("Bearer", jwt);
    using var form = new MultipartFormDataContent();
    var img = new ByteArrayContent(jpeg);
    img.Headers.ContentType = new("image/jpeg");
    form.Add(img, "image", "wound.jpg");
    var resp = await http.PostAsync($"{Base}/api/v1/segment/escalate", form);
    resp.EnsureSuccessStatusCode();
    using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
    var png = Convert.FromBase64String(doc.RootElement.GetProperty("mask_png_b64").GetString());
    return PngToBoolMask(png, 127);                     // >127 前景
}
```

## 7. 回歸測試(CI 守門)
- `engineering/phase2/test_dual_track_integration.py`:用 5 張快取機率重現端到端路由,**斷言路由後平均 Dice ≥ 0.88、優於純端上、難例(student<0.6)皆 escalate**。
- 實跑結果:端上純 student 0.656 → **路由後 0.900**,escalate 3/5;`pytest` 1 passed。
- 飛輪每輪重訓後須先過此測試(見 `retrain_flywheel_SOP`)再放行新權重。
