# SBOM 與開源授權清冊

> 由 `engineering/phase2/generate_sbom.py` 產生，日期 2026-08-04。

> **這不是法律意見。** 授權欄位取自套件 metadata 或人工對照表，
> 正式送審前請由熟悉授權條款的人複核，特別是 GPL/AGPL 系與資料集的使用條款。


## 1. 後端 Python 相依

| 套件 | 版本限制 | 已安裝 | 授權 |
|---|---|---|---|
| `flask` | >=3.0.0 | 3.1.3 | BSD-3-Clause |
| `flask-cors` | >=4.0.0 | — | MIT |
| `flask-jwt-extended` | >=4.6.0 | 4.7.4 | MIT License |
| `numpy` | >=1.24.0 | 2.2.6 | BSD License |
| `opencv-python-headless` | >=4.8.0 | 4.13.0.92 | Apache Software License |
| `Pillow` | >=10.0.0 | 12.2.0 | MIT-CMU |
| `requests` | >=2.31.0 | 2.34.2 | Apache Software License |
| `werkzeug` | >=3.0.0 | 3.1.8 | BSD-3-Clause |
| `gunicorn` | >=21.2.0 | — | MIT |
| `google-cloud-storage` | >=2.14.0 | — | Apache-2.0 |
| `onnxruntime` | >=1.17.0 | 1.23.2 | MIT License |

## 2. Android 相依

| 座標 | 用途 | 授權 |
|---|---|---|
| `androidx.core:core-ktx:1.12.0` | implementation | Apache-2.0 |
| `androidx.lifecycle:lifecycle-runtime-ktx:2.7.0` | implementation | Apache-2.0 |
| `androidx.activity:activity-compose:1.8.2` | implementation | Apache-2.0 |
| `androidx.compose.ui:ui` | implementation | Apache-2.0 |
| `androidx.compose.ui:ui-graphics` | implementation | Apache-2.0 |
| `androidx.compose.ui:ui-tooling-preview` | implementation | Apache-2.0 |
| `androidx.compose.material3:material3` | implementation | Apache-2.0 |
| `androidx.compose.material:material-icons-extended` | implementation | Apache-2.0 |
| `androidx.compose.foundation:foundation` | implementation | Apache-2.0 |
| `androidx.camera:camera-core:1.3.1` | implementation | Apache-2.0 |
| `androidx.camera:camera-camera2:1.3.1` | implementation | Apache-2.0 |
| `androidx.camera:camera-lifecycle:1.3.1` | implementation | Apache-2.0 |
| `androidx.camera:camera-view:1.3.1` | implementation | Apache-2.0 |
| `androidx.camera:camera-extensions:1.3.1` | implementation | Apache-2.0 |
| `com.quickbirdstudios:opencv:4.5.3.0` | implementation | Apache-2.0（OpenCV 4.5+ 本體與此 Android 包裝皆為 Apache-2.0） |
| `org.tensorflow:tensorflow-lite:2.14.0` | implementation | Apache-2.0 |
| `org.tensorflow:tensorflow-lite-support:0.4.4` | implementation | Apache-2.0 |
| `org.tensorflow:tensorflow-lite-metadata:0.4.4` | implementation | Apache-2.0 |
| `com.microsoft.onnxruntime:onnxruntime-android:1.18.0` | implementation | MIT |
| `androidx.room:room-runtime:2.6.1` | implementation | Apache-2.0 |
| `androidx.room:room-ktx:2.6.1` | implementation | Apache-2.0 |
| `androidx.room:room-compiler:2.6.1` | ksp | Apache-2.0 |
| `com.squareup.retrofit2:retrofit:2.9.0` | implementation | Apache-2.0 |
| `com.squareup.retrofit2:converter-gson:2.9.0` | implementation | Apache-2.0 |
| `com.squareup.okhttp3:okhttp:4.12.0` | implementation | Apache-2.0 |
| `com.google.code.gson:gson:2.10.1` | implementation | Apache-2.0 |
| `com.github.bumptech.glide:glide:4.16.0` | implementation | BSD-2-Clause + MIT + Apache-2.0（混合，見其 LICENSE） |
| `com.github.bumptech.glide:compose:1.0.0-beta01` | implementation | Apache-2.0 |
| `com.google.accompanist:accompanist-permissions:0.32.0` | implementation | Apache-2.0 |
| `com.github.PhilJay:MPAndroidChart:v3.1.0` | implementation | Apache-2.0 |
| `com.journeyapps:zxing-android-embedded:4.3.0` | implementation | Apache-2.0 |
| `com.google.zxing:core:3.5.2` | implementation | Apache-2.0 |
| `com.google.android.material:material:1.11.0` | implementation | Apache-2.0 |
| `androidx.test.ext:junit:1.1.5` | androidTestImplementation | Apache-2.0 |
| `androidx.test.espresso:espresso-core:3.5.1` | androidTestImplementation | Apache-2.0 |
| `androidx.compose.ui:ui-test-junit4` | androidTestImplementation | Apache-2.0 |
| `androidx.compose.ui:ui-tooling` | debugImplementation | Apache-2.0 |
| `androidx.compose.ui:ui-test-manifest` | debugImplementation | Apache-2.0 |

### 可移除的相依

每個第三方元件都是「要說明來源、要追 CVE」的長期負擔。
用不到還留著，只是在審查時多幾個要回答的問題。

| 座標 | 為什麼可能用不到 |
|---|---|
| `com.github.PhilJay:MPAndroidChart:v3.1.0` | 時間軸的趨勢圖改為自繪 Canvas，未再使用 |
| `com.journeyapps:zxing-android-embedded:4.3.0` | 條碼掃描屬 DoctorAuthActivity（已無入口） |

## 3. 模型與訓練資料出處

這一節是審查最在意的部分，也是最容易在臨時整理時漏掉的——
模型權重不在任何套件管理器裡。

### `student_fp16.onnx`

| 項目 | 內容 |
|---|---|
| 用途 | 端上/後端主力分割（蒸餾 student） |
| 架構來源 | segmentation_models_pytorch (qubvel) — MIT |
| 權重來源 | 本專案自行訓練（蒸餾自 A-UNet / UNet++ 集成） |
| 訓練資料 | uwm wound-segmentation + FUSC 公開資料集（授權條款待逐項確認） |
| 備註 | 權重為自訓產物，架構為開源 MIT。資料集授權須在正式送審前逐項核對。 |

### `a_unet.onnx`

| 項目 | 內容 |
|---|---|
| 用途 | 難例升級集成成員 A |
| 架構來源 | UNet (segmentation_models_pytorch) — MIT |
| 權重來源 | 本專案自行訓練 |
| 訓練資料 | 同上 |

### `unetpp.onnx`

| 項目 | 內容 |
|---|---|
| 用途 | 難例升級集成成員 B |
| 架構來源 | UNet++ (segmentation_models_pytorch) — MIT |
| 權重來源 | 本專案自行訓練 |
| 訓練資料 | 同上 |
| 備註 | 推論耗時佔集成路由 63%，是延遲優化的第一目標。 |


## 4. 待確認

- 套件層級授權皆已標註。

- **訓練資料集的授權條款**（uwm wound-segmentation、FUSC）須逐項核對，
  特別是「是否允許商業使用」與「衍生模型的授權要求」。這一項無法自動化。
- 已知漏洞掃描（CVE）不在本腳本範圍，建議另跑 `pip-audit` 與 Gradle 的依賴檢查。
