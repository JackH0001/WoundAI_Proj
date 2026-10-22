package com.woundmeasurement.app.data.database

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.woundmeasurement.app.data.crypto.PhiCrypto
import com.woundmeasurement.app.data.store.MedicalAccountPreferences
import com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue
import com.woundmeasurement.app.data.store.MedicalDataScope
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class MedicalAccountPreferencesTest {
    private class TestContext(base: Context) : ContextWrapper(base) {
        private val prefix = "synthetic-prefs-${UUID.randomUUID()}-"
        override fun getApplicationContext(): Context = this
        override fun getSharedPreferences(name: String, mode: Int): SharedPreferences =
            super.getSharedPreferences(prefix + name, mode)
        fun clear() { MedicalDataScope.entries.forEach { super.deleteSharedPreferences(prefix + it.settingsName) } }
    }
    private fun context() = TestContext(ApplicationProvider.getApplicationContext())
    @Test fun withdrawalWaitSurvivesRestartAndIsSeparateFromRestoreAndDemo() {
        val ctx=context()
        try {
            val a=MedicalAccountPreferences(ctx)
            a.deferRetry(ConsentQueue.WITHDRAWAL,1234567)
            assertEquals(1234567,MedicalAccountPreferences(ctx).retryAfter(ConsentQueue.WITHDRAWAL))
            assertEquals(0,a.retryAfter(ConsentQueue.RESTORE))
            assertEquals(0,MedicalAccountPreferences(ctx,MedicalDataScope.SYNTHETIC_REVIEW).retryAfter(ConsentQueue.WITHDRAWAL))
        } finally {ctx.clear()}
    }
    @Test fun encryptedCredentialsSurviveReopenAndClearOnlyTheirScope() {
        val ctx = context()
        try {
            val a = MedicalAccountPreferences(ctx)
            val b = MedicalAccountPreferences(ctx, MedicalDataScope.SYNTHETIC_REVIEW)
            val pa = UUID.randomUUID().toString(); val pb = UUID.randomUUID().toString()
            assertTrue(a.setCredentials("fixture-a", pa)); assertTrue(b.setCredentials("fixture-b", pb))
            val prefs = ctx.getSharedPreferences(MedicalDataScope.MMH.settingsName, Context.MODE_PRIVATE)
            assertNotEquals(pa, prefs.getString("backend_pass_enc", null))
            val reopened = MedicalAccountPreferences(ctx, MedicalDataScope.SYNTHETIC_REVIEW)
            assertEquals("fixture-b", reopened.user()); assertEquals(pb, reopened.password())
            a.clearCredentials()
            assertFalse(a.hasCredentials()); assertEquals("", a.password())
            assertEquals(pb, reopened.password())
        } finally { ctx.clear() }
    }
    @Test fun legacyEncryptedKeysRemainReadableAndCorruptionFailsClosed() {
        val ctx = context()
        try {
            val p = UUID.randomUUID().toString()
            val prefs = ctx.getSharedPreferences("woundai_settings", Context.MODE_PRIVATE)
            prefs.edit().putString("backend_user", "existing-fixture")
                .putString("backend_pass_enc", PhiCrypto.encrypt(p))
                .putStringSet("pending_withdrawals", setOf("same-code")).commit()
            val store = MedicalAccountPreferences(ctx)
            assertEquals("existing-fixture", store.user()); assertEquals(p, store.password())
            assertEquals(setOf("same-code"), store.pending(ConsentQueue.WITHDRAWAL))
            listOf("broken-ciphertext", "enc1:broken-ciphertext").forEach { invalid ->
                prefs.edit().putString("backend_pass_enc", invalid).commit()
                assertEquals("", store.password())
                assertFalse(store.hasCredentials())
            }
            assertEquals("", MedicalAccountPreferences(ctx, MedicalDataScope.SYNTHETIC_REVIEW).user())
        } finally { ctx.clear() }
    }
    @Test fun latestIntentSupersedesOppositeInItsOwnScope() {
        val ctx = context()
        try {
            val a = MedicalAccountPreferences(ctx)
            val b = MedicalAccountPreferences(ctx, MedicalDataScope.SYNTHETIC_REVIEW)
            listOf(a,b).forEach { it.add(listOf("same-code"), ConsentQueue.WITHDRAWAL); it.add(listOf("same-code"), ConsentQueue.RESTORE) }
            a.remove("same-code", ConsentQueue.WITHDRAWAL)
            assertTrue(a.pending(ConsentQueue.WITHDRAWAL).isEmpty())
            assertEquals(setOf("same-code"), a.pending(ConsentQueue.RESTORE))
            assertTrue(b.pending(ConsentQueue.WITHDRAWAL).isEmpty())
            assertEquals(setOf("same-code"), b.pending(ConsentQueue.RESTORE))
        } finally { ctx.clear() }
    }
    @Test fun oldAcknowledgementCannotClearNewIntent() {
        val ctx=context()
        try {
            val a=MedicalAccountPreferences(ctx)
            a.add(listOf("code"),ConsentQueue.RESTORE);val old=a.token("code",ConsentQueue.RESTORE)!!
            a.add(listOf("code"),ConsentQueue.WITHDRAWAL);a.add(listOf("code"),ConsentQueue.RESTORE)
            a.remove("code",ConsentQueue.RESTORE,old)
            assertEquals(setOf("code"),a.pending(ConsentQueue.RESTORE))
            assertNotEquals(old,a.token("code",ConsentQueue.RESTORE))
            assertEquals(a.token("code",ConsentQueue.RESTORE),MedicalAccountPreferences(ctx).token("code",ConsentQueue.RESTORE))
        } finally {ctx.clear()}
    }
    @Test fun ambiguousLegacyPairKeepsWithdrawalWithoutResurrectingRestore() {
        val ctx=context()
        try {
            ctx.getSharedPreferences(MedicalDataScope.MMH.settingsName,Context.MODE_PRIVATE).edit()
                .putStringSet("pending_withdrawals",setOf("legacy")).putStringSet("pending_restores",setOf("legacy")).commit()
            val a=MedicalAccountPreferences(ctx)
            assertEquals(setOf("legacy"),a.pending(ConsentQueue.WITHDRAWAL))
            assertTrue(a.pending(ConsentQueue.RESTORE).isEmpty())
            a.remove("legacy",ConsentQueue.WITHDRAWAL)
            assertTrue(MedicalAccountPreferences(ctx).pending(ConsentQueue.RESTORE).isEmpty())
        } finally {ctx.clear()}
    }
    @Test fun concurrentWritersDoNotLoseConsentCodes() {
        val ctx = context(); val pool = Executors.newFixedThreadPool(4)
        try {
            val stores = (0..3).map { MedicalAccountPreferences(ctx, MedicalDataScope.SYNTHETIC_REVIEW) }
            val jobs = (0..99).map { i -> pool.submit { stores[i % 4].add(listOf("fixture-$i"), ConsentQueue.WITHDRAWAL) } }
            jobs.forEach { it.get(10, TimeUnit.SECONDS) }
            assertEquals(100, stores[0].pending(ConsentQueue.WITHDRAWAL).size)
            assertTrue(stores[0].pending(ConsentQueue.RESTORE).isEmpty())
        } finally { pool.shutdownNow(); ctx.clear() }
    }
}
