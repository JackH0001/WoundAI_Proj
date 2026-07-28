package com.woundmeasurement.app.data.crypto

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 病患可識別資訊(PII/PHI)的欄位級加密。
 *
 * 設計取捨(見 docs/sprint_N1_case_management_design.md §3)：
 *  - 金鑰由 **Android Keystore** 產生且**不可匯出**，隨 App 移除而失效
 *    → 手機遺失/備份外流/其他 App 讀走 DB 檔，拿到的都是密文。
 *  - 用 AES-256-GCM(帶驗證標籤)：既保密也防竄改，改一個位元就解不開。
 *  - 每筆自己的 12-byte IV，與密文串在一起存(`IV || ciphertext+tag`，Base64)。
 *    **絕不可固定 IV**——GCM 重複 IV 會直接洩漏明文關係。
 *  - `setUserAuthenticationRequired(false)`：若要求每次生物辨識，背景寫入會拋例外，
 *    醫護在量測流程中被打斷。安全性由「手機本身的鎖屏 + Keystore 硬體保護」承擔。
 *
 * **不使用 SQLCipher 的理由**：需新增原生相依、增大 APK，而該專案的 Gradle/AGP 組合
 * 剛穩定(wrapper 釘 8.13)，不值得為此再冒建置風險。整庫加密列為 N2。
 *
 * **誠實邊界**：這擋的是「靜態資料外洩」。擋不了 App 執行中被 root 取得記憶體，
 * 也不等於 HIPAA/GDPR 的完整技術保護措施。
 */
object PhiCrypto {

    private const val KEY_ALIAS = "woundai_phi_v1"
    private const val MAC_ALIAS = "woundai_mrn_hmac_v1"
    private const val ANDROID_KEYSTORE = "AndroidKeyStore"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val IV_LEN = 12          // GCM 標準 IV 長度
    private const val TAG_BITS = 128
    /** 密文前綴：讓「已加密」與「v1 舊明文」在同一欄位裡可以區分(見 decrypt 的相容處理)。 */
    private const val PREFIX = "enc1:"

    // 每次呼叫都 KeyStore.getInstance()+load(null)+getEntry() 是三次 keystore daemon 的 binder 往返;
    // 清單解密會呼叫上百次 → 主執行緒直接卡住。金鑰物件本身是輕量代理,快取安全。
    @Volatile private var cachedKey: SecretKey? = null
    @Volatile private var cachedMac: SecretKey? = null

    private fun key(): SecretKey {
        cachedKey?.let { return it }
        synchronized(this) {
            cachedKey?.let { return it }
            val ks = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
            val existing = (ks.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey
            val k = existing ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
                .apply {
                    init(
                        KeyGenParameterSpec.Builder(
                            KEY_ALIAS,
                            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                        )
                            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                            .setKeySize(256)
                            .setUserAuthenticationRequired(false)
                            .build()
                    )
                }.generateKey()
            cachedKey = k
            return k
        }
    }

    /** 病歷號 HMAC 用的 Keystore 金鑰（不可匯出）。 */
    private fun macKey(): SecretKey {
        cachedMac?.let { return it }
        synchronized(this) {
            cachedMac?.let { return it }
            val ks = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
            val existing = (ks.getEntry(MAC_ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey
            val k = existing ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_HMAC_SHA256, ANDROID_KEYSTORE)
                .apply {
                    init(
                        KeyGenParameterSpec.Builder(MAC_ALIAS, KeyProperties.PURPOSE_SIGN)
                            .setUserAuthenticationRequired(false)
                            .build()
                    )
                }.generateKey()
            cachedMac = k
            return k
        }
    }

    /** 明文 → `enc1:Base64(IV||密文+tag)`。null/空字串原樣回傳(不必要地加密空值只會增加失敗面)。 */
    fun encrypt(plain: String?): String? {
        if (plain.isNullOrEmpty()) return plain
        return try {
            val c = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
            val out = c.iv + c.doFinal(plain.toByteArray(Charsets.UTF_8))
            PREFIX + Base64.encodeToString(out, Base64.NO_WRAP)
        } catch (e: Exception) {
            // 加密失敗**不可**默默存明文——那會讓 DB 裡混入未受保護的 PHI 且無人察覺
            throw IllegalStateException("PHI 加密失敗:${e.message}", e)
        }
    }

    /**
     * 密文 → 明文。
     * 沒有 `enc1:` 前綴者視為 **v1 舊資料的明文**原樣回傳——
     * migration 之後仍可能存在(升級時不強制回填)，硬解會讓舊病歷讀不出來。
     */
    fun decrypt(stored: String?): String? {
        if (stored.isNullOrEmpty()) return stored
        if (!stored.startsWith(PREFIX)) return stored
        return try {
            val raw = Base64.decode(stored.removePrefix(PREFIX), Base64.NO_WRAP)
            if (raw.size <= IV_LEN) return null
            val c = Cipher.getInstance(TRANSFORM).apply {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, raw, 0, IV_LEN))
            }
            String(c.doFinal(raw, IV_LEN, raw.size - IV_LEN), Charsets.UTF_8)
        } catch (e: Exception) {
            // 金鑰被清(App 重裝/還原備份)→ 解不開。回 null 讓 UI 顯示「無法解密」，
            // 而不是讓整個病患清單崩潰。
            null
        }
    }

    /** 是否已加密(供稽核/測試檢查 DB 內容用)。 */
    fun isEncrypted(stored: String?): Boolean = stored?.startsWith(PREFIX) == true

    /**
     * 病歷號的不可逆指紋，供**查重與比對**(不還原)。
     *
     * ⚠ 這裡用 **Keystore HMAC-SHA256**，不是加鹽 SHA-256。
     * 原因：病歷號的熵極低（院內通常是固定格式的短碼），而寫死在原始碼裡的鹽對攻擊者是公開的
     * → 加鹽雜湊擋不住字典攻擊，幾秒就能把全表反查回明文。
     * HMAC 的金鑰在 Keystore 裡**不可匯出**，拿到 DB 檔也算不出對應值。
     *
     * 副作用：App 重裝後金鑰換新，舊的 mrnHash 就對不起來（查重會失效，但加密欄位同樣解不開，
     * 那時整份本機病歷本來就已不可用）。
     */
    fun hashMrn(mrn: String?): String? {
        if (mrn.isNullOrBlank()) return null
        return try {
            val mac = javax.crypto.Mac.getInstance("HmacSHA256").apply { init(macKey()) }
            mac.doFinal(mrn.trim().toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it) }
        } catch (e: Exception) {
            throw IllegalStateException("病歷號指紋計算失敗:${e.message}", e)
        }
    }

    /** 二進位 PHI(如手寫簽名 PNG)的加密。回 `IV||密文+tag`,直接存 BLOB。 */
    fun encryptBytes(plain: ByteArray?): ByteArray? {
        if (plain == null || plain.isEmpty()) return plain
        return try {
            val c = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
            c.iv + c.doFinal(plain)
        } catch (e: Exception) {
            throw IllegalStateException("簽名加密失敗:${e.message}", e)
        }
    }

    /** 解 [encryptBytes]。解不開回 null（金鑰已失效），不讓畫面崩潰。 */
    fun decryptBytes(stored: ByteArray?): ByteArray? {
        if (stored == null || stored.size <= IV_LEN) return null
        return try {
            val c = Cipher.getInstance(TRANSFORM).apply {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, stored, 0, IV_LEN))
            }
            c.doFinal(stored, IV_LEN, stored.size - IV_LEN)
        } catch (e: Exception) {
            null
        }
    }

    /** 顯示用遮蔽：`A123456789` → `A12***789`。清單上不要把完整病歷號攤在螢幕上。 */
    fun maskMrn(mrn: String?): String {
        val s = mrn?.trim().orEmpty()
        if (s.length <= 4) return if (s.isEmpty()) "—" else "*".repeat(s.length)
        return s.take(3) + "***" + s.takeLast(3)
    }
}
