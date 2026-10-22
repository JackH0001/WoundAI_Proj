package com.woundmeasurement.app.data.store

import android.content.Context
import android.content.SharedPreferences
import com.woundmeasurement.app.data.crypto.PhiCrypto
import com.woundmeasurement.app.pipeline.ResearchCapacityWait

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
    fun retryAfter(queue:ConsentQueue):Long = synchronized(lock) {
        val raw=prefs.getLong(queue.key+"_retry_at",0)
        val bounded=ResearchCapacityWait.privacyDeadline(raw)
        if(raw!=bounded) check(prefs.edit().putLong(queue.key+"_retry_at",bounded).commit())
        bounded
    }
    fun deferRetry(queue:ConsentQueue,until:Long) = synchronized(lock) { check(prefs.edit().putLong(queue.key+"_retry_at",ResearchCapacityWait.privacyDeadline(until)).commit()) }
    private fun intents(): org.json.JSONObject {
        prefs.getString("consent_intents_v1", null)?.let { return org.json.JSONObject(it) }
        val result = org.json.JSONObject()
        for(queue in listOf(ConsentQueue.RESTORE, ConsentQueue.WITHDRAWAL)) {
            for(code in prefs.getStringSet(queue.key, emptySet()) ?: emptySet()) {
                result.put(code, org.json.JSONObject().put("queue",queue.key).put("token",java.util.UUID.randomUUID().toString()))
            }
        }
        check(prefs.edit().putString("consent_intents_v1",result.toString()).commit())
        return result
    }
    fun pending(queue: ConsentQueue): Set<String> = synchronized(lock) {
        val values=intents()
        values.keys().asSequence().filter { values.getJSONObject(it).getString("queue")==queue.key }.toSet()
    }
    fun token(code:String, queue:ConsentQueue):String? = synchronized(lock) {
        val entry=intents().optJSONObject(code)
        if(entry?.getString("queue")==queue.key) entry.getString("token") else null
    }
    fun add(codes: Collection<String>, queue: ConsentQueue) = synchronized(lock) {
        val next=intents()
        for(code in codes.map {it.trim()}.filter {it.isNotBlank()}) {
            next.put(code,org.json.JSONObject().put("queue",queue.key).put("token",java.util.UUID.randomUUID().toString()))
        }
        check(prefs.edit().putString("consent_intents_v1",next.toString()).commit())
    }
    fun remove(code: String, queue: ConsentQueue, expectedToken:String?=null) = synchronized(lock) {
        val next=intents();val entry=next.optJSONObject(code)
        if(entry?.getString("queue")==queue.key && (expectedToken==null || entry.getString("token")==expectedToken)) {
            next.remove(code)
            check(prefs.edit().putString("consent_intents_v1",next.toString()).commit())
        }
    }
}
