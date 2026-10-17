package com.woundmeasurement.app.data.store

import android.content.Context
import android.content.SharedPreferences
import com.woundmeasurement.app.data.crypto.PhiCrypto

/** Immutable settings namespace; shared lock covers read-modify-write across instances. */
class MedicalAccountPreferences private constructor(
    private val prefs: SharedPreferences,
    private val invalidate: () -> Unit,
    private val encrypt: (String) -> String?,
    private val decrypt: (String) -> String?
) {
    constructor(context: Context, scope: MedicalDataScope = MedicalDataScope.MMH,
        invalidate: () -> Unit = {}) : this(
        context.applicationContext.getSharedPreferences(scope.settingsName, Context.MODE_PRIVATE),
        invalidate, { PhiCrypto.encrypt(it) }, { PhiCrypto.decrypt(it) })
    companion object { private val lock = Any() }
    fun user(): String = synchronized(lock) { prefs.getString("backend_user", "") ?: "" }
    fun password(): String = synchronized(lock) {
        val value = prefs.getString("backend_pass_enc", null) ?: return@synchronized ""
        if (!PhiCrypto.isEncrypted(value)) return@synchronized ""
        runCatching { decrypt(value) }.getOrNull() ?: ""
    }
    fun hasCredentials(): Boolean = synchronized(lock) {
        user().isNotBlank() && password().isNotEmpty()
    }
    fun setCredentials(user: String, password: String): Boolean = synchronized(lock) {
        val value = runCatching { encrypt(password) }.getOrNull()?.takeIf { PhiCrypto.isEncrypted(it) }
            ?: return@synchronized false
        invalidate()
        prefs.edit().putString("backend_user", user.trim()).putString("backend_pass_enc", value).apply()
        true
    }
    fun clearCredentials() = synchronized(lock) {
        invalidate()
        prefs.edit().remove("backend_user").remove("backend_pass_enc").apply()
    }
    enum class ConsentQueue(val key: String) {
        WITHDRAWAL("pending_withdrawals"), RESTORE("pending_restores")
    }
    fun retryAfter(queue:ConsentQueue):Long = synchronized(lock) { prefs.getLong(queue.key+"_retry_at",0) }
    fun deferRetry(queue:ConsentQueue,until:Long) = synchronized(lock) { check(prefs.edit().putLong(queue.key+"_retry_at",until).commit()) }
    fun pending(queue: ConsentQueue): Set<String> = synchronized(lock) {
        prefs.getStringSet(queue.key, emptySet())?.toSet() ?: emptySet()
    }
    fun add(codes: Collection<String>, queue: ConsentQueue) = synchronized(lock) {
        val next = pending(queue).toMutableSet().apply { addAll(codes.filter { it.isNotBlank() }) }
        prefs.edit().putStringSet(queue.key, next).apply()
    }
    fun remove(code: String, queue: ConsentQueue) = synchronized(lock) {
        val next = pending(queue).toMutableSet().apply { remove(code) }
        prefs.edit().putStringSet(queue.key, next).apply()
    }
}
