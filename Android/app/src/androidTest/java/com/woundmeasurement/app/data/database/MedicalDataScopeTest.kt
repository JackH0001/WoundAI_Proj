package com.woundmeasurement.app.data.database

import android.content.Context
import android.content.ContextWrapper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.LocalImageStore
import com.woundmeasurement.app.data.store.MedicalDataScope
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.Date
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class MedicalDataScopeTest {
    private class IsolatedContext(base: Context, val root: File) : ContextWrapper(base) {
        override fun getApplicationContext(): Context = this
        override fun getFilesDir(): File = File(root, "files").apply { mkdirs() }
        override fun getDatabasePath(name: String): File = File(root, "databases/$name").also { it.parentFile!!.mkdirs() }
    }
    private fun context(): IsolatedContext {
        val app = ApplicationProvider.getApplicationContext<Context>()
        return IsolatedContext(app, File(app.cacheDir, "scope-test-${UUID.randomUUID()}").apply { mkdirs() })
    }
    private fun measurement(note: String) = MeasurementEntity(patientId = null, timestamp = Date(0),
        hasWound = true, confidence = 1.0, estimatedArea = 10.0, estimatedVolume = null,
        woundType = null, quality = "synthetic", processingTime = 0, imagePath = "same.enc",
        dataPath = "", notes = note, source = "phantom")

    @Test fun legacyNamesRemainAndSameRecordIDsAreIsolated() = runBlocking {
        val ctx = context()
        val mmh = WoundMeasurementDatabase.getDatabase(ctx)
        val review = WoundMeasurementDatabase.getDatabase(ctx, MedicalDataScope.SYNTHETIC_REVIEW)
        try {
            assertEquals("wound_measurement_database", MedicalDataScope.MMH.databaseName)
            assertEquals("wound_images", MedicalDataScope.MMH.imageDirectory)
            assertSame(mmh, WoundMeasurementDatabase.getDatabase(ctx, MedicalDataScope.MMH))
            assertNotSame(mmh, review)
            val a = mmh.measurementDao().insertMeasurement(measurement("MMH fixture"))
            val b = review.measurementDao().insertMeasurement(measurement("review fixture"))
            assertEquals(a, b)
            assertEquals("MMH fixture", mmh.measurementDao().getById(a)?.notes)
            assertEquals("review fixture", review.measurementDao().getById(b)?.notes)
            assertTrue(ctx.getDatabasePath(MedicalDataScope.MMH.databaseName).exists())
            assertTrue(ctx.getDatabasePath(MedicalDataScope.SYNTHETIC_REVIEW.databaseName).exists())
        } finally { mmh.close(); review.close(); ctx.root.deleteRecursively() }
    }
    @Test fun sameMediaNameDoesNotCrossReadOrDelete() {
        val ctx = context()
        try {
            val a = LocalImageStore(ctx)
            val b = LocalImageStore(ctx, MedicalDataScope.SYNTHETIC_REVIEW)
            for ((scope, store, bytes) in listOf(Triple(MedicalDataScope.MMH, a, byteArrayOf(1,2)),
                Triple(MedicalDataScope.SYNTHETIC_REVIEW, b, byteArrayOf(3,4)))) {
                val name = requireNotNull(store.save(bytes))
                val dir = File(ctx.filesDir, scope.imageDirectory)
                assertTrue(File(dir, name).renameTo(File(dir, "same.enc")))
            }
            assertArrayEquals(byteArrayOf(1,2), a.rawBytes("same.enc"))
            assertArrayEquals(byteArrayOf(3,4), b.rawBytes("same.enc"))
            assertTrue(a.delete("same.enc"))
            assertNull(a.rawBytes("same.enc"))
            assertArrayEquals(byteArrayOf(3,4), b.rawBytes("same.enc"))
        } finally { ctx.root.deleteRecursively() }
    }
    @Test fun mediaTraversalCannotReadOrDeleteAnotherScope() {
        val ctx = context()
        try {
            val a = LocalImageStore(ctx)
            val b = LocalImageStore(ctx, MedicalDataScope.SYNTHETIC_REVIEW)
            val name = requireNotNull(a.save(byteArrayOf(9)))
            val traversal = "../../../wound_images/$name"
            assertFalse(b.exists(traversal)); assertNull(b.rawBytes(traversal)); assertFalse(b.delete(traversal))
            assertArrayEquals(byteArrayOf(9), a.rawBytes(name))
        } finally { ctx.root.deleteRecursively() }
    }
}
