package com.woundmeasurement.app.pipeline

import android.content.Context
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import com.woundmeasurement.app.BuildConfig
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.repo.CaseRepository
import com.woundmeasurement.app.data.store.*
import com.woundmeasurement.app.institution.SyntheticReviewInterceptor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/** Retained by a screen tree and its pending tasks. Never retargeted on environment changes. */
class MedicalRuntime internal constructor(context: Context, val scope: MedicalDataScope = MedicalDataScope.MMH,
    private val clientFactory: (String, () -> Boolean, Boolean) -> BackendClient = { origin,current,review -> BackendClient.forRuntime(origin,current,review) }) {
    private val app = context.applicationContext
    private val open = AtomicBoolean(true)
    private val generation = AtomicLong()
    val needsGoogle = BuildConfig.MMH_VALIDATION && scope == MedicalDataScope.MMH
    val needsLiveSession = BuildConfig.MMH_VALIDATION || scope == MedicalDataScope.SYNTHETIC_REVIEW
    val fixedOrigin = needsLiveSession
    val title = if (scope == MedicalDataScope.SYNTHETIC_REVIEW) "合成示範環境" else "MMH 機構服務"
    val accounts = MedicalAccountPreferences(app, scope) { generation.incrementAndGet() }
    val database by lazy { WoundMeasurementDatabase.getDatabase(app, scope) }
    val repo by lazy { CaseRepository.from(database) }
    val images by lazy { LocalImageStore(app, scope) }
    @Volatile private var origin = if (scope == MedicalDataScope.MMH) AppSettings.backendUrl(app) else SyntheticReviewInterceptor.ORIGIN
    @Volatile private var lastOkAt = 0L
    @Volatile private var degradation: String? = null
    fun addPendingWithdrawals(codes: List<String>) = accounts.add(codes, MedicalAccountPreferences.ConsentQueue.WITHDRAWAL)
    fun addPendingRestores(codes: List<String>) = accounts.add(codes, MedicalAccountPreferences.ConsentQueue.RESTORE)
    fun backendUrl() = origin
    fun backendUser() = accounts.user()
    fun backendPassword() = accounts.password()
    fun hasCredentials() = accounts.hasCredentials()
    fun setCredentials(user: String, password: String) = open.get() && accounts.setCredentials(user, password)
    fun clearCredentials() = accounts.clearCredentials()
    fun setBackendUrl(value: String) {
        if (fixedOrigin || !open.get()) return
        val normalized = AppSettings.normalizeUrl(value)
        if (origin != normalized) generation.incrementAndGet()
        AppSettings.setBackendUrl(app, normalized); origin = normalized
    }
    private val researchDelegate=lazy { ClinicalResearchController(app,this) }
    internal val research get()=researchDelegate.value
    internal fun isOpen() = open.get()
    suspend fun exportSavedRecord(row: com.woundmeasurement.app.data.entity.MeasurementEntity): String? {
        val exports = com.woundmeasurement.app.data.store.PrivateExports(app, scope)
        if (!exports.enabled) return null
        return try {
            SavedMeasurementExport(this, exports).save(row)?.let { "行政副本已依時間軸保存版本建立。" }
        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (_: Exception) { "時間軸紀錄已保留；行政副本未能完整建立，請稍後重試。" }
    }
    fun deactivate() { open.set(false); generation.incrementAndGet(); if(researchDelegate.isInitialized()) research.close() }
    fun operation(): () -> Boolean {
        val epoch = generation.get(); val google = MedicalSessionEpoch.snapshot()
        return { open.get() && generation.get() == epoch && (!needsGoogle || google == MedicalSessionEpoch.snapshot()) }
    }
    fun makeBackend(base: String = origin): BackendClient {
        if (fixedOrigin && base != origin) throw IOException("不可切換此環境的固定網址")
        return clientFactory(base, operation(), scope == MedicalDataScope.SYNTHETIC_REVIEW)
    }
    private val pool by lazy { MedicalSessionPool(input = {
        MedicalSessionPool.Input(if (open.get()) origin else "", backendUser(), backendPassword(),
            generation.get() + if (needsGoogle) MedicalSessionEpoch.snapshot() else 0L)
    }, factory = { makeBackend(it) }) }
    fun acquire(): BackendClient = acquireVerified().client
    fun acquireVerified(): MedicalSessionPool.Verified<BackendClient> {
        if (!open.get()) throw IOException("原環境已關閉")
        if (needsLiveSession) return pool.acquireVerified()
        val current = operation(); val client = makeBackend()
        if (!client.login(backendUser(), backendPassword()) || !current()) throw IOException("登入未完成")
        return MedicalSessionPool.Verified(client, client.identity ?: throw IOException("登入身分尚未確認"))
    }
    suspend fun warmup() = withContext(Dispatchers.IO) {
        val current = operation()
        val health = runCatching { makeBackend().health() }.getOrNull()
        if (health != null && current()) { lastOkAt = System.currentTimeMillis(); degradation = health.degradedReason }
    }
    fun sinceLastOkMs() = if (lastOkAt == 0L) Long.MAX_VALUE else System.currentTimeMillis() - lastOkAt
    fun degradedBanner() = degradation?.let { "⚠ 後端服務降級：$it。請通知管理者；結果暫不作參考。" }
}

val LocalMedicalRuntime = staticCompositionLocalOf<MedicalRuntime?> { null }
@Composable fun rememberMedicalRuntime(): MedicalRuntime {
    val supplied = LocalMedicalRuntime.current
    val context = LocalContext.current
    return supplied ?: remember { MedicalRuntime(context) }
}
